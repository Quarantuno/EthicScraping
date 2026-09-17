#!/usr/bin/env python3
"""Runs the data-awareness evaluation prompts (prompts.jsonl) against a
base or fine-tuned model and saves the responses for human review.

This deliberately does NOT auto-score the model: "does this model talk
about data ethics sensibly" is a judgment call, not something a
hardcoded metric should decide. Read `results.jsonl` yourself, one row
per prompt, against that prompt's `rubric` field.

Usage:
    # Validate the prompts file only, no model needed:
    python eval/run_eval.py --check

    # Run the base model:
    python eval/run_eval.py --base-model Qwen/Qwen2.5-0.5B

    # Run a fine-tuned model (base + LoRA adapter from training/train_lora.py):
    python eval/run_eval.py --base-model Qwen/Qwen2.5-0.5B \\
        --adapter training/output/final_adapter
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

HERE = Path(__file__).parent


def load_prompts(path: str) -> List[dict]:
    prompts = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                prompts.append(json.loads(line))
    return prompts


def validate_prompts(prompts: List[dict]) -> List[str]:
    """Structural checks only (no model involved), so this can run
    without any of the heavy training/eval dependencies installed."""
    problems = []
    required = {"id", "category", "prompt", "rubric"}
    seen_ids = set()

    if not prompts:
        problems.append("Il file dei prompt e' vuoto.")

    for i, p in enumerate(prompts):
        missing = required - p.keys()
        if missing:
            problems.append(f"Prompt #{i}: campi mancanti {sorted(missing)}")
            continue
        if not str(p["prompt"]).strip():
            problems.append(f"Prompt #{i} (id={p['id']}): campo 'prompt' vuoto")
        if p["id"] in seen_ids:
            problems.append(f"Prompt #{i}: id duplicato '{p['id']}'")
        seen_ids.add(p["id"])

    return problems


def generate_responses(prompts: List[dict], base_model: str,
                        adapter_dir: Optional[str] = None,
                        max_new_tokens: int = 200) -> List[dict]:
    """Import pesanti fatti qui apposta: cosi' --check resta utilizzabile
    senza torch/transformers/peft installati."""
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        print(
            "Dipendenze mancanti. Installa con:\n"
            "    pip install -r training/requirements-training.txt\n"
            f"(errore originale: {exc})",
            file=sys.stderr,
        )
        raise SystemExit(1)

    tokenizer = AutoTokenizer.from_pretrained(adapter_dir or base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(base_model)

    if adapter_dir:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, adapter_dir)

    model.eval()

    results = []
    for p in prompts:
        inputs = tokenizer(p["prompt"], return_tensors="pt")
        with torch.no_grad():
            output_ids = model.generate(
                **inputs, max_new_tokens=max_new_tokens, do_sample=False,
                pad_token_id=tokenizer.pad_token_id,
            )
        response = tokenizer.decode(
            output_ids[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True
        )
        results.append({**p, "response": response.strip()})

    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompts", default=str(HERE / "prompts.jsonl"))
    parser.add_argument("--base-model", help="Modello base Hugging Face (richiesto senza --check)")
    parser.add_argument("--adapter", help="Cartella dell'adapter LoRA da training/train_lora.py (opzionale)")
    parser.add_argument("--output", default=str(HERE / "results.jsonl"))
    parser.add_argument("--max-new-tokens", type=int, default=200)
    parser.add_argument(
        "--check", action="store_true",
        help="Valida solo il file dei prompt, senza caricare alcun modello "
             "(non richiede torch/transformers/peft installati)",
    )
    args = parser.parse_args(argv)

    prompts = load_prompts(args.prompts)
    problems = validate_prompts(prompts)

    if problems:
        print("Problemi nel file dei prompt:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    categories = sorted(set(p["category"] for p in prompts))
    print(f"{len(prompts)} prompt caricati da {args.prompts} "
          f"({len(categories)} categorie: {', '.join(categories)}).")

    if args.check:
        print("File dei prompt valido.")
        return 0

    if not args.base_model:
        print("Serve --base-model per generare le risposte (oppure usa --check).",
              file=sys.stderr)
        return 1

    results = generate_responses(prompts, args.base_model, args.adapter,
                                  args.max_new_tokens)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"Risposte salvate in {out_path}. Rivedile a mano contro il campo 'rubric' di ogni riga.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
