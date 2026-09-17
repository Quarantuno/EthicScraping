# Phase 2 — LoRA fine-tuning

*[Leggi questo in italiano](README.it.md)*

Scaffold for fine-tuning an open source model on the dataset collected
by the scraping pipeline (phase 1). **This has not been run end-to-end**
in the environment it was written in: it needs a GPU (or at least time,
even on CPU for small models) and a network connection to download a
base model from Hugging Face — neither was available there. The config
and data-prep logic is tested (see `tests/test_prepare_dataset.py` and
`tests/test_train_lora_check.py`); the actual training run needs to be
verified on your own hardware.

## Rule one: only train on reviewed data

Don't run training directly on the raw `dataset/output.jsonl`. First:

```bash
python main.py review --dataset dataset/output.jsonl
```

This produces `dataset/output_approved.jsonl` (only the records you
approved by hand) and `dataset/output_rejected.jsonl`. It's the
`*_approved.jsonl` file that should feed into phase 2.

## 1. Prepare the data

```bash
python training/prepare_dataset.py \
  --input dataset/output_approved.jsonl \
  --output-dir training/data
```

Splits the text into manageable chunks and produces
`training/data/train.jsonl` and `training/data/val.jsonl` (format
`{"text": "..."}`, compatible with `datasets.load_dataset("json", ...)`).

## 2. Install training dependencies

Kept separate from `requirements.txt` because they're heavy (torch,
transformers...):

```bash
pip install -r training/requirements-training.txt
```

On Mac (Apple Silicon / MPS) or CPU-only, skip `bitsandbytes` (it's
meant for NVIDIA/CUDA GPUs): it stays commented out in the requirements
file — only enable it if you have a CUDA GPU and want 4/8-bit
quantization.

## 3. Configure

```bash
cp training/config.example.yaml training/config.yaml
```

At minimum, change `base_model` to match your hardware: a 0.5-1.5B
parameter model for a CPU/Mac test run, a larger one only with adequate
GPU. The comments in the file explain the other fields.

Validate the config before downloading or training anything:

```bash
python training/train_lora.py --config training/config.yaml --check
```

## 4. Train

```bash
python training/train_lora.py --config training/config.yaml
```

Saves a LoRA adapter (not the whole model) to
`training/output/final_adapter/`. To use it for inference:

```python
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

base = AutoModelForCausalLM.from_pretrained("<base_model from your config>")
model = PeftModel.from_pretrained(base, "training/output/final_adapter")
tokenizer = AutoTokenizer.from_pretrained("training/output/final_adapter")
```

## 5. (Optional) Evaluate data awareness

Once you have a trained (or even just the base) model, `../eval/` has a
small set of prompts probing how the model talks about data provenance,
consent, and ethical data use — the actual point of this project, not
just "a model". See `../eval/README.md`.

## A note on "awareness"

The point of this project isn't just to train *a* model, but to train
one on data whose provenance, license, and PII exposure are known (see
the main README). It's worth evaluating the resulting model on direct
questions about ethical data use and about its own training data's
provenance before calling it "done" — see `../eval/` for a starting
point.
