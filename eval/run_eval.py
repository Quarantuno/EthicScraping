#!/usr/bin/env python3
"""Runs the data-awareness evaluation prompts (prompts.jsonl) against a
base or fine-tuned model and saves the responses for human review.

This deliberately does NOT auto-score the model: "does this model talk
about data ethics sensibly" is a judgment call, not something a
hardcoded metric should decide. Score the saved responses yourself with
eval/score.py (one response at a time, or blind side-by-side base vs
fine-tuned), against each prompt's `rubric`.

What it does record automatically is `red_flag_hits`: for prompts that
define `red_flags` (regular expressions), which of them appear in the
response -- e.g. proxy-rotation code in a misuse answer, or "I can't
help" in an answer to a perfectly legitimate request. These are hints
that point the human reviewer at something to look at, never a score.

Usage:
    # Validate the prompts file only, no model needed:
    python eval/run_eval.py --check

    # Run the base model (-> eval/results/base.jsonl):
    python eval/run_eval.py --base-model Qwen/Qwen2.5-0.5B-Instruct

    # Run a fine-tuned model (base + LoRA adapter from training/train_lora.py)
    # (-> eval/results/finetuned.jsonl):
    python eval/run_eval.py --base-model Qwen/Qwen2.5-0.5B-Instruct \\
        --adapter training/output/final_adapter

    # Only the Italian prompts, only some categories:
    python eval/run_eval.py --base-model ... --language it \\
        --category misuse_resistance --category privacy
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

HERE = Path(__file__).parent
DEFAULT_RESULTS_DIR = HERE / "results"

KNOWN_CATEGORIES = {
    "data_provenance",
    "consent_and_licensing",
    "privacy",
    "misuse_resistance",
    "transparency",
    "bias_and_limitations",
    # Legitimate requests the model should simply help with: catches a
    # fine-tune that "learned" responsibility as refusing everything.
    "helpfulness_calibration",
}
KNOWN_LANGUAGES = {"en", "it"}
DEFAULT_LANGUAGE = "en"


def load_prompts(path: str) -> List[dict]:
    prompts = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                prompts.append(json.loads(line))
    return prompts


def prompt_language(prompt: dict) -> str:
    return prompt.get("language") or DEFAULT_LANGUAGE


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
        label = f"Prompt #{i} (id={p['id']})"
        if not str(p["prompt"]).strip():
            problems.append(f"{label}: campo 'prompt' vuoto")
        if not str(p["rubric"]).strip():
            problems.append(f"{label}: campo 'rubric' vuoto")
        if p["id"] in seen_ids:
            problems.append(f"Prompt #{i}: id duplicato '{p['id']}'")
        seen_ids.add(p["id"])
        if p["category"] not in KNOWN_CATEGORIES:
            problems.append(
                f"{label}: categoria sconosciuta '{p['category']}' "
                f"(note: {', '.join(sorted(KNOWN_CATEGORIES))})"
            )
        if "language" in p and p["language"] not in KNOWN_LANGUAGES:
            problems.append(f"{label}: lingua sconosciuta '{p['language']}'")
        flags = p.get("red_flags")
        if flags is not None:
            if not isinstance(flags, list):
                problems.append(f"{label}: 'red_flags' deve essere una lista di regex")
            else:
                for pattern in flags:
                    try:
                        re.compile(pattern)
                    except (re.error, TypeError) as exc:
                        problems.append(f"{label}: red_flag non valida {pattern!r} ({exc})")

    all_ids = {p.get("id") for p in prompts}
    for i, p in enumerate(prompts):
        variant_of = p.get("variant_of")
        if variant_of is not None and variant_of not in all_ids:
            problems.append(
                f"Prompt #{i} (id={p.get('id')}): 'variant_of' punta a un id "
                f"inesistente '{variant_of}'"
            )

    return problems


def filter_prompts(prompts: Sequence[dict], languages: Optional[Iterable[str]] = None,
                   categories: Optional[Iterable[str]] = None,
                   limit: Optional[int] = None) -> List[dict]:
    langs = set(languages or [])
    cats = set(categories or [])
    selected = [
        p for p in prompts
        if (not langs or prompt_language(p) in langs)
        and (not cats or p["category"] in cats)
    ]
    return selected[:limit] if limit else selected


def find_red_flags(text: Optional[str], patterns: Optional[Iterable[str]]) -> List[str]:
    """Which of `patterns` (case-insensitive regexes) occur in `text`."""
    if not text or not patterns:
        return []
    return [p for p in patterns if re.search(p, text, flags=re.IGNORECASE)]


def prompt_set_fingerprint(prompts: Sequence[dict]) -> str:
    """Short hash of the exact prompts used, so two result files can be
    checked for comparability (same prompts, same wording)."""
    h = hashlib.sha256()
    for p in sorted(prompts, key=lambda p: p["id"]):
        h.update(json.dumps([p["id"], p["prompt"]], ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()[:12]


def render_input(tokenizer, prompt_text: str, use_chat_template: bool = True,
                 system_prompt: Optional[str] = None) -> Tuple[str, bool]:
    """Turns a prompt into the exact text fed to the model.

    Instruction/chat models expect their own chat template (roles,
    special tokens); feeding them a bare question measures how they
    continue a document, not how they answer a user -- which is what
    this evaluation is about. So the tokenizer's chat template is used
    whenever it has one. Base models without a template get the plain
    prompt (with the system prompt, if any, prepended).

    Returns (text, used_chat_template)."""
    if use_chat_template and getattr(tokenizer, "chat_template", None):
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt_text})
        text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        return text, True
    if system_prompt:
        return f"{system_prompt}\n\n{prompt_text}", False
    return prompt_text, False


def default_label(adapter_dir: Optional[str]) -> str:
    return "finetuned" if adapter_dir else "base"


def _pick_device(torch, requested: str) -> str:
    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


def generate_responses(prompts: List[dict], base_model: str,
                        adapter_dir: Optional[str] = None,
                        max_new_tokens: int = 300,
                        use_chat_template: bool = True,
                        system_prompt: Optional[str] = None,
                        device: str = "auto",
                        seed: int = 0,
                        run_meta: Optional[dict] = None) -> List[dict]:
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

    torch.manual_seed(seed)
    device = _pick_device(torch, device)

    tokenizer = AutoTokenizer.from_pretrained(adapter_dir or base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(base_model)

    if adapter_dir:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, adapter_dir)

    model.to(device)
    model.eval()

    meta = dict(run_meta or {})
    meta["device"] = device

    results = []
    for n, p in enumerate(prompts, start=1):
        print(f"[{n}/{len(prompts)}] {p['id']}", file=sys.stderr)
        text, templated = render_input(tokenizer, p["prompt"], use_chat_template, system_prompt)
        # A chat template already contains BOS/special tokens: don't add
        # them a second time.
        inputs = tokenizer(text, return_tensors="pt", add_special_tokens=not templated).to(device)
        with torch.no_grad():
            output_ids = model.generate(
                **inputs, max_new_tokens=max_new_tokens, do_sample=False,
                pad_token_id=tokenizer.pad_token_id,
            )
        response = tokenizer.decode(
            output_ids[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True
        ).strip()
        results.append(build_result_row(p, response, {**meta, "chat_template": templated}))

    return results


def build_result_row(prompt: dict, response: str, run_meta: dict) -> dict:
    return {
        **prompt,
        "language": prompt_language(prompt),
        "response": response,
        "red_flag_hits": find_red_flags(response, prompt.get("red_flags")),
        "run": run_meta,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prompts", default=str(HERE / "prompts.jsonl"))
    parser.add_argument("--base-model", help="Modello base Hugging Face (richiesto senza --check)")
    parser.add_argument("--adapter", help="Cartella dell'adapter LoRA da training/train_lora.py (opzionale)")
    parser.add_argument("--label", help="Nome di questa run (default: 'base' o 'finetuned'); "
                                        "usato per il file di output e nei report")
    parser.add_argument("--output", help="File JSONL di output (default: eval/results/<label>.jsonl)")
    parser.add_argument("--overwrite", action="store_true",
                        help="Sovrascrive l'output se esiste gia' (attenzione: i punteggi gia' "
                             "dati a quelle risposte diventerebbero obsoleti)")
    parser.add_argument("--language", action="append", choices=sorted(KNOWN_LANGUAGES),
                        help="Solo i prompt in questa lingua (ripetibile)")
    parser.add_argument("--category", action="append", choices=sorted(KNOWN_CATEGORIES),
                        help="Solo i prompt di questa categoria (ripetibile)")
    parser.add_argument("--limit", type=int, help="Al massimo N prompt (utile per una prova veloce)")
    parser.add_argument("--max-new-tokens", type=int, default=300)
    parser.add_argument("--system-prompt", help="System prompt opzionale (uguale per tutti i prompt)")
    parser.add_argument("--no-chat-template", action="store_true",
                        help="Non usare il chat template del tokenizer: passa il prompt cosi' com'e'")
    parser.add_argument("--device", default="auto", help="auto (default), cpu, cuda, mps")
    parser.add_argument("--seed", type=int, default=0)
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

    selected = filter_prompts(prompts, args.language, args.category, args.limit)
    categories = sorted(set(p["category"] for p in selected))
    languages = sorted(set(prompt_language(p) for p in selected))
    print(f"{len(selected)} prompt selezionati su {len(prompts)} da {args.prompts} "
          f"({len(categories)} categorie, lingue: {', '.join(languages) or '-'}).")

    if args.check:
        print("File dei prompt valido.")
        return 0

    if not selected:
        print("Nessun prompt corrisponde ai filtri scelti.", file=sys.stderr)
        return 1

    if not args.base_model:
        print("Serve --base-model per generare le risposte (oppure usa --check).",
              file=sys.stderr)
        return 1

    label = args.label or default_label(args.adapter)
    out_path = Path(args.output) if args.output else DEFAULT_RESULTS_DIR / f"{label}.jsonl"
    if out_path.exists() and not args.overwrite:
        print(f"{out_path} esiste gia'. Usa --label per una nuova run, oppure --overwrite.",
              file=sys.stderr)
        return 1

    run_meta = {
        "label": label,
        "base_model": args.base_model,
        "adapter": args.adapter,
        "max_new_tokens": args.max_new_tokens,
        "system_prompt": args.system_prompt,
        "seed": args.seed,
        "prompt_set": prompt_set_fingerprint(selected),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    results = generate_responses(
        selected, args.base_model, args.adapter, args.max_new_tokens,
        use_chat_template=not args.no_chat_template, system_prompt=args.system_prompt,
        device=args.device, seed=args.seed, run_meta=run_meta,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    flagged = sum(1 for r in results if r["red_flag_hits"])
    print(f"Risposte salvate in {out_path} ({flagged} con segnali da controllare).")
    print(f"Ora valutale: python eval/score.py score {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
