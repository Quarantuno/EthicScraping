# Fase 2 — Fine-tuning LoRA

*[Read this in English](README.md)*

Scaffold per addestrare un modello open source sul dataset raccolto con
la pipeline di scraping (fase 1). **Non e' stato eseguito end-to-end**
nell'ambiente in cui e' stato scritto: serve una GPU (o comunque tempo,
anche su CPU per modelli piccoli) e la connessione per scaricare un
modello base da Hugging Face, cose non disponibili in quell'ambiente.
La logica di configurazione e preparazione dati e' testata (vedi
`tests/test_prepare_dataset.py` e `tests/test_train_lora_check.py`); il
training vero va verificato sul tuo hardware.

## Prima regola: allena solo su dati rivisti

Non lanciare il training direttamente su `dataset/output.jsonl` grezzo.
Prima:

```bash
python main.py review --dataset dataset/output.jsonl
```

Questo produce `dataset/output_approved.jsonl` (solo i record che hai
approvato a mano) e `dataset/output_rejected.jsonl`. E' il file
`*_approved.jsonl` che va usato come input della fase 2.

## 1. Prepara i dati

```bash
python training/prepare_dataset.py \
  --input dataset/output_approved.jsonl \
  --output-dir training/data
```

Divide il testo in blocchi gestibili e produce `training/data/train.jsonl`
e `training/data/val.jsonl` (formato `{"text": "..."}`, compatibile con
`datasets.load_dataset("json", ...)`).

## 2. Installa le dipendenze di training

Separate da `requirements.txt` perche' pesanti (torch, transformers...):

```bash
pip install -r training/requirements-training.txt
```

Su Mac (Apple Silicon / MPS) o CPU-only, salta `bitsandbytes` (e'
pensato per GPU NVIDIA/CUDA): resta commentato nel file di requirements,
attivalo solo se hai una GPU CUDA e vuoi la quantizzazione 4/8-bit.

## 3. Configura

```bash
cp training/config.example.yaml training/config.yaml
```

Modifica almeno `base_model` in base al tuo hardware: un modello da
0.5-1.5B parametri per una prova su CPU/Mac, uno piu' grande solo con
una GPU adeguata. I commenti nel file spiegano gli altri campi.

Verifica la config prima di scaricare/allenare nulla:

```bash
python training/train_lora.py --config training/config.yaml --check
```

## 4. Allena

```bash
python training/train_lora.py --config training/config.yaml
```

Salva un adapter LoRA (non l'intero modello) in
`training/output/final_adapter/`. Per usarlo in inferenza:

```python
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

base = AutoModelForCausalLM.from_pretrained("<base_model usato in config>")
model = PeftModel.from_pretrained(base, "training/output/final_adapter")
tokenizer = AutoTokenizer.from_pretrained("training/output/final_adapter")
```

## 5. (Facoltativo) Valuta la "consapevolezza" sui dati

L'addestramento ha davvero reso il modello migliore su ciò di cui tratta
questo progetto — e non solo più incline a rifiutare? Fai girare gli
stessi prompt sul modello base e su base + adapter, poi confrontali alla
cieca:

```bash
python eval/run_eval.py --base-model <base_model>                                  # -> eval/results/base.jsonl
python eval/run_eval.py --base-model <base_model> --adapter training/output/final_adapter   # -> eval/results/finetuned.jsonl
python eval/score.py compare eval/results/base.jsonl eval/results/finetuned.jsonl
python eval/score.py report --compare eval/results/base.jsonl:eval/results/finetuned.jsonl
```

Vedi `../eval/README.it.md` per dare un punteggio a ogni run singolarmente,
e per cosa guardare nel report.

## Nota sulla "consapevolezza"

Il punto del progetto non e' solo addestrare *un* modello, ma addestrarlo
su dati di cui si conosce provenienza, licenza e presenza di PII (vedi il
README principale). Vale la pena, prima di considerare il modello
"pronto", valutarlo anche su domande dirette sull'uso etico dei dati e
sulla propria provenienza dei dati di training -- un piccolo set di
prompt di valutazione per questo non e' ancora incluso: e' uno dei
prossimi passi naturali una volta che il training di base funziona.
