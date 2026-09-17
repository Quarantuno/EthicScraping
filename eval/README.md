# Data-awareness evaluation

A small set of prompts (`prompts.jsonl`, 18 prompts across 6 categories)
probing how a model talks about the *actual point of this project*:
data provenance, consent, licensing, privacy, and the limits of
web-scraped training data. This is not a benchmark with a numeric score
— it's a structured starting point for a human to read the model's
answers against a rubric and judge them.

## Categories

- `data_provenance` — does it overclaim certainty about its own training
  data, or handle the question honestly?
- `consent_and_licensing` — does it treat scraping/reuse permissions as
  something that matters?
- `privacy` — does it handle personal data requests appropriately?
- `misuse_resistance` — does it decline to help bypass robots.txt,
  rate limits, logins, or build covert profiling tools?
- `transparency` — does it clearly identify as an AI and acknowledge its
  own limitations?
- `bias_and_limitations` — does it engage honestly with the biases and
  gaps inherent in web-scraped training data?

## Usage

Validate the prompts file only (no model needed):

```bash
python eval/run_eval.py --check
```

Run against a base model:

```bash
python eval/run_eval.py --base-model Qwen/Qwen2.5-0.5B
```

Run against a model fine-tuned with `training/train_lora.py`:

```bash
python eval/run_eval.py \
  --base-model Qwen/Qwen2.5-0.5B \
  --adapter training/output/final_adapter
```

Both need `training/requirements-training.txt` installed (torch,
transformers, and peft for the adapter case).

Results are written to `results.jsonl` (git-ignored), one row per
prompt: the original prompt, its `rubric`, and the model's `response`.
Read it yourself — there's no automatic scoring here on purpose, since
"is this a responsible answer about data ethics" is exactly the kind of
judgment call that shouldn't be reduced to a keyword match.

## Extending this

The prompt set is intentionally small and a starting point, not a
finished benchmark. Good additions: more adversarial misuse-resistance
prompts, prompts in other languages, or prompts specific to the actual
sources in your dataset (e.g. "what's in the data you were trained on
about [topic X you scraped]?").
