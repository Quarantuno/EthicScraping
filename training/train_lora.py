#!/usr/bin/env python3
"""Fine-tuning LoRA di un modello base open source sul dataset prodotto
dalla pipeline di scraping (fase 2 del progetto).

ATTENZIONE: questo script non e' stato eseguito end-to-end nell'ambiente
in cui e' stato scritto (serve una GPU/CPU con tempo a disposizione e il
download di un modello da internet). E' stata validata la logica di
configurazione e caricamento dati con `--check`; il training vero va
verificato sul tuo hardware prima di fidarti del risultato.

Uso:
    # Verifica config + file dati senza scaricare nulla ne' richiedere
    # torch/transformers installati:
    python training/train_lora.py --config training/config.yaml --check

    # Training vero (richiede training/requirements-training.txt installato):
    python training/train_lora.py --config training/config.yaml
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

import yaml


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_config(cfg: dict) -> List[str]:
    """Controlli su file/campi attesi, senza toccare torch/transformers:
    cosi' `--check` funziona anche prima di installare le dipendenze
    pesanti di training/requirements-training.txt."""
    problems = []

    if not cfg.get("base_model"):
        problems.append("Manca 'base_model' in config.")

    data_cfg = cfg.get("data", {})
    train_file = data_cfg.get("train_file")
    if not train_file:
        problems.append("Manca 'data.train_file' in config.")
    elif not Path(train_file).exists():
        problems.append(
            f"'{train_file}' non esiste. Esegui prima "
            f"training/prepare_dataset.py per generarlo a partire dal "
            f"dataset scrappato (idealmente quello revisionato)."
        )

    val_file = data_cfg.get("val_file")
    if val_file and not Path(val_file).exists():
        problems.append(
            f"'{val_file}' indicato in config ma non trovato "
            f"(rimuovilo dalla config se non hai un validation set)."
        )

    lora_cfg = cfg.get("lora", {})
    if not lora_cfg.get("target_modules"):
        problems.append("Manca 'lora.target_modules' in config.")

    training_cfg = cfg.get("training", {})
    if not training_cfg.get("output_dir"):
        problems.append("Manca 'training.output_dir' in config.")

    return problems


def run_training(cfg: dict) -> None:
    """Costruisce dataset, modello, LoRA config e lancia il training.
    Import pesanti fatti qui apposta (non a livello di modulo), cosi'
    `--check` resta utilizzabile senza queste dipendenze installate."""
    try:
        from datasets import load_dataset
        from peft import LoraConfig, get_peft_model
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            DataCollatorForLanguageModeling,
            Trainer,
            TrainingArguments,
        )
    except ImportError as exc:
        print(
            "Dipendenze di training mancanti. Installa con:\n"
            "    pip install -r training/requirements-training.txt\n"
            f"(errore originale: {exc})",
            file=sys.stderr,
        )
        raise SystemExit(1)

    base_model = cfg["base_model"]
    data_cfg = cfg["data"]
    lora_cfg = cfg.get("lora", {})
    training_cfg = cfg.get("training", {})

    print(f"Carico tokenizer e modello base: {base_model}")
    tokenizer = AutoTokenizer.from_pretrained(base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(base_model)

    peft_config = LoraConfig(
        r=lora_cfg.get("r", 16),
        lora_alpha=lora_cfg.get("alpha", 32),
        lora_dropout=lora_cfg.get("dropout", 0.05),
        target_modules=lora_cfg.get("target_modules", ["q_proj", "v_proj"]),
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, peft_config)
    model.print_trainable_parameters()

    data_files = {"train": data_cfg["train_file"]}
    if data_cfg.get("val_file") and Path(data_cfg["val_file"]).exists():
        data_files["validation"] = data_cfg["val_file"]

    raw_datasets = load_dataset("json", data_files=data_files)

    max_seq_length = data_cfg.get("max_seq_length", 1024)

    def tokenize(batch):
        return tokenizer(
            batch["text"], truncation=True, max_length=max_seq_length,
            padding="max_length",
        )

    tokenized = raw_datasets.map(
        tokenize, batched=True,
        remove_columns=raw_datasets["train"].column_names,
    )

    collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)

    args = TrainingArguments(
        output_dir=training_cfg.get("output_dir", "training/output"),
        num_train_epochs=training_cfg.get("num_train_epochs", 3),
        per_device_train_batch_size=training_cfg.get("per_device_train_batch_size", 2),
        gradient_accumulation_steps=training_cfg.get("gradient_accumulation_steps", 8),
        learning_rate=training_cfg.get("learning_rate", 2e-4),
        logging_steps=training_cfg.get("logging_steps", 10),
        save_steps=training_cfg.get("save_steps", 200),
        eval_strategy="steps" if "validation" in tokenized else "no",
        eval_steps=training_cfg.get("eval_steps", 200),
        fp16=training_cfg.get("fp16", False),
        bf16=training_cfg.get("bf16", False),
        report_to=[],
    )

    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=tokenized["train"],
        eval_dataset=tokenized.get("validation"),
        data_collator=collator,
    )

    trainer.train()

    adapter_dir = Path(training_cfg.get("output_dir", "training/output")) / "final_adapter"
    model.save_pretrained(str(adapter_dir))
    tokenizer.save_pretrained(str(adapter_dir))
    print(f"Adapter LoRA salvato in: {adapter_dir}")
    print(
        "Per usarlo in inferenza: carica il modello base con "
        "AutoModelForCausalLM.from_pretrained(...), poi applica l'adapter "
        "con PeftModel.from_pretrained(model, '<adapter_dir>')."
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="File YAML di configurazione")
    parser.add_argument(
        "--check", action="store_true",
        help="Valida config e file dati senza avviare il training "
             "(non richiede torch/transformers installati)",
    )
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    problems = validate_config(cfg)

    if problems:
        print("Problemi nella configurazione:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    if args.check:
        print("Config valida. Pronto per il training "
              "(rilancia senza --check per avviarlo davvero).")
        return 0

    run_training(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
