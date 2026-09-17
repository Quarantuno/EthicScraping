#!/usr/bin/env python3
"""Trasforma il dataset JSONL prodotto dallo scraping (idealmente la
versione *_approved.jsonl uscita da `python main.py review`) in due file
train/val pronti per l'addestramento: blocchi di testo di lunghezza
gestibile, in formato {"text": "..."} compatibile con
`datasets.load_dataset("json", ...)`.

Deliberatamente senza dipendenze pesanti (solo stdlib): questo script
puo' girare ovunque, anche senza torch/transformers installati, ed e'
testabile senza scaricare nulla.

Uso:
    python training/prepare_dataset.py \\
        --input dataset/output_approved.jsonl \\
        --output-dir training/data
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


def load_records(path: str) -> List[dict]:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def chunk_text(text: str, max_words: int = 400, overlap_words: int = 0) -> List[str]:
    """Divide un testo lungo in blocchi di al massimo `max_words` parole.

    Un semplice conteggio di parole e' una proxy imprecisa per il numero
    di token del tokenizer che userai in fase di training, ma evita di
    dover scaricare un tokenizer specifico solo per preparare i dati:
    scegli `max_words` con un margine di sicurezza rispetto alla
    `max_seq_length` che configurerai in train_lora.py.
    """
    words = text.split()
    if not words:
        return []
    if len(words) <= max_words:
        return [text]

    step = max_words - overlap_words
    if step <= 0:
        raise ValueError("overlap_words deve essere minore di max_words")

    chunks = []
    for start in range(0, len(words), step):
        chunk_words = words[start:start + max_words]
        if not chunk_words:
            break
        chunks.append(" ".join(chunk_words))
        if start + max_words >= len(words):
            break
    return chunks


def build_examples(records: Iterable[dict], max_words: int = 400) -> List[Dict]:
    """Da record del dataset a esempi di training, con un minimo di
    provenienza portata avanti (utile per debug, non usata come input
    del modello)."""
    examples = []
    for record in records:
        text = (record.get("text") or "").strip()
        if not text:
            continue
        for chunk in chunk_text(text, max_words=max_words):
            examples.append({
                "text": chunk,
                "source_url": record.get("url"),
                "license": record.get("license"),
            })
    return examples


def split_examples(examples: List[Dict], val_ratio: float = 0.05,
                    seed: int = 42) -> Tuple[List[Dict], List[Dict]]:
    if not 0 <= val_ratio < 1:
        raise ValueError("val_ratio deve essere in [0, 1)")

    shuffled = examples[:]
    random.Random(seed).shuffle(shuffled)

    if len(shuffled) < 2 or val_ratio == 0:
        return shuffled, []

    n_val = max(1, round(len(shuffled) * val_ratio))
    return shuffled[n_val:], shuffled[:n_val]


def write_jsonl(path: str, examples: List[Dict]) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for ex in examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Dataset JSONL di input")
    parser.add_argument("--output-dir", required=True, help="Cartella dove scrivere train.jsonl/val.jsonl")
    parser.add_argument("--max-words", type=int, default=400, help="Parole massime per blocco di testo")
    parser.add_argument("--val-ratio", type=float, default=0.05, help="Frazione di esempi per la validation")
    parser.add_argument("--seed", type=int, default=42, help="Seed per lo shuffle train/val")
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if "approved" not in input_path.name:
        print(
            f"ATTENZIONE: '{input_path.name}' non sembra un dataset revisionato "
            f"(atteso un file '*_approved.jsonl' prodotto da 'python main.py "
            f"review'). Proseguo comunque, ma valuta di rivedere manualmente "
            f"i contenuti prima di addestrare un modello su dati non "
            f"controllati.",
            file=sys.stderr,
        )

    records = load_records(str(input_path))
    examples = build_examples(records, max_words=args.max_words)
    train, val = split_examples(examples, val_ratio=args.val_ratio, seed=args.seed)

    train_path = str(Path(args.output_dir) / "train.jsonl")
    val_path = str(Path(args.output_dir) / "val.jsonl")
    write_jsonl(train_path, train)
    write_jsonl(val_path, val)

    print(f"Record di input: {len(records)}")
    print(f"Esempi totali (dopo chunking): {len(examples)}")
    print(f"Train: {len(train)} -> {train_path}")
    print(f"Val:   {len(val)} -> {val_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
