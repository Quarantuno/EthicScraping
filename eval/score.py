#!/usr/bin/env python3
"""Human scoring for eval/run_eval.py results -- the part of the
evaluation that is deliberately NOT automated.

Three subcommands:

  score RESULTS.jsonl
      Shows one response at a time with its prompt, rubric and any
      red-flag hints, and asks for a score:
          2 = pass     (meets the rubric)
          1 = partial  (right direction, but incomplete/hedged/flawed)
          0 = fail     (contradicts the rubric)
      plus an optional one-line note. Saved immediately next to the
      results (RESULTS.scores.json), so you can stop and resume; a score
      is tied to the exact response text it was given for, so if you
      regenerate a response its old score is not silently reused.

  compare A.jsonl B.jsonl
      Blind side-by-side comparison -- the question that matters after
      fine-tuning is "did it get better than the base model?", and
      answering that while knowing which answer is which invites bias.
      For each prompt both runs answered, the two responses are shown
      as "Risposta 1" / "Risposta 2" in an order that is pseudo-random
      but fixed per prompt (seeded), and you pick 1 / 2 / tie / both
      bad. Which run was which is only revealed in the report.

  report RESULTS.jsonl [...] [--compare COMPARE.json ...] [--markdown OUT.md]
      Per run: mean score and pass rate per category and per language,
      how many responses had red-flag hints, how many are still
      unscored. Per comparison: wins / ties / both-bad per category.
      Small samples are small samples: the report shows counts next to
      every percentage on purpose.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

SCORE_LABELS = {2: "pass", 1: "partial", 0: "fail"}
COMPARE_CHOICES = {"1", "2", "t", "b"}  # risposta 1, risposta 2, tie, both bad


# --- loading -------------------------------------------------------------

def load_results(path: str) -> List[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def results_label(rows: Sequence[dict], path: str) -> str:
    for r in rows:
        label = (r.get("run") or {}).get("label")
        if label:
            return label
    return Path(path).stem


def response_hash(text: Optional[str]) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def default_scores_path(results_path: str) -> Path:
    p = Path(results_path)
    return p.with_name(p.stem + ".scores.json")


def _read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


# --- absolute scores -------------------------------------------------------

class ScoreStore:
    """Scores for one results file, keyed by prompt id, each bound to the
    hash of the response it was given for."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = _read_json(self.path, {"scores": {}})
        self.data.setdefault("scores", {})

    def get(self, row: dict) -> Optional[dict]:
        entry = self.data["scores"].get(row["id"])
        if entry and entry.get("response_sha") == response_hash(row.get("response")):
            return entry
        return None

    def is_stale(self, row: dict) -> bool:
        entry = self.data["scores"].get(row["id"])
        return bool(entry) and entry.get("response_sha") != response_hash(row.get("response"))

    def set(self, row: dict, score: int, note: str = "") -> None:
        if score not in SCORE_LABELS:
            raise ValueError(f"Punteggio non valido: {score!r} (ammessi: 0, 1, 2)")
        self.data["scores"][row["id"]] = {
            "score": score,
            "note": note,
            "response_sha": response_hash(row.get("response")),
            "scored_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        _write_json(self.path, self.data)

    def pending(self, rows: Sequence[dict]) -> List[dict]:
        return [r for r in rows if self.get(r) is None]


# --- blind pairwise comparison ---------------------------------------------

def a_shown_first(seed: int, prompt_id: str) -> bool:
    """Deterministic per (seed, prompt): resuming a session, or reviewing
    prompts in a different order, never reshuffles what you saw."""
    digest = hashlib.sha256(f"{seed}:{prompt_id}".encode("utf-8")).digest()
    return digest[0] % 2 == 0


def default_compare_path(label_a: str, label_b: str, results_dir: Path) -> Path:
    return results_dir / f"compare__{label_a}__vs__{label_b}.json"


class CompareStore:
    def __init__(self, path: Path, label_a: str, label_b: str, seed: int = 0):
        self.path = Path(path)
        self.data = _read_json(self.path, {})
        if self.data and (self.data.get("a") != label_a or self.data.get("b") != label_b):
            raise ValueError(
                f"{self.path} confronta '{self.data.get('a')}' vs '{self.data.get('b')}', "
                f"non '{label_a}' vs '{label_b}'. Usa un altro --out."
            )
        self.data.setdefault("a", label_a)
        self.data.setdefault("b", label_b)
        self.data.setdefault("seed", seed)
        self.data.setdefault("decisions", {})

    @property
    def seed(self) -> int:
        return self.data["seed"]

    @staticmethod
    def _pair_hash(row_a: dict, row_b: dict) -> str:
        return response_hash((row_a.get("response") or "") + "\x00" + (row_b.get("response") or ""))

    def get(self, row_a: dict, row_b: dict) -> Optional[dict]:
        entry = self.data["decisions"].get(row_a["id"])
        if entry and entry.get("pair_sha") == self._pair_hash(row_a, row_b):
            return entry
        return None

    def decide(self, row_a: dict, row_b: dict, choice: str, note: str = "") -> dict:
        """`choice` is what the reviewer typed for the *displayed* order
        ("1", "2", "t" tie, "b" both bad); it is resolved to a / b here,
        so the stored decision no longer depends on display order."""
        if choice not in COMPARE_CHOICES:
            raise ValueError(f"Scelta non valida: {choice!r}")
        a_first = a_shown_first(self.seed, row_a["id"])
        if choice == "t":
            winner = "tie"
        elif choice == "b":
            winner = "both_bad"
        elif (choice == "1") == a_first:
            winner = "a"
        else:
            winner = "b"
        entry = {
            "winner": winner,
            "shown_first": "a" if a_first else "b",
            "note": note,
            "pair_sha": self._pair_hash(row_a, row_b),
            "decided_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        self.data["decisions"][row_a["id"]] = entry
        _write_json(self.path, self.data)
        return entry


def paired_rows(rows_a: Sequence[dict], rows_b: Sequence[dict]):
    """(row_a, row_b) for every prompt id present in both runs, in A's
    order. Prompts only one run answered are skipped (and counted by
    the caller)."""
    by_id_b = {r["id"]: r for r in rows_b}
    return [(ra, by_id_b[ra["id"]]) for ra in rows_a if ra["id"] in by_id_b]


# --- reporting ---------------------------------------------------------------

def _bucket():
    return {"n": 0, "scored": 0, "total": 0, "pass": 0, "fail": 0, "flagged": 0}


def summarize_scores(rows: Sequence[dict], store: ScoreStore) -> dict:
    by_category: Dict[str, dict] = OrderedDict()
    by_language: Dict[str, dict] = OrderedDict()
    overall = _bucket()
    stale = 0
    for r in rows:
        entry = store.get(r)
        if entry is None and store.is_stale(r):
            stale += 1
        targets = [
            overall,
            by_category.setdefault(r["category"], _bucket()),
            by_language.setdefault(r.get("language") or "en", _bucket()),
        ]
        for b in targets:
            b["n"] += 1
            if r.get("red_flag_hits"):
                b["flagged"] += 1
            if entry is not None:
                b["scored"] += 1
                b["total"] += entry["score"]
                b["pass"] += entry["score"] == 2
                b["fail"] += entry["score"] == 0
    return {"overall": overall, "by_category": by_category,
            "by_language": by_language, "stale": stale}


def summarize_comparison(rows_a: Sequence[dict], rows_b: Sequence[dict],
                         store: CompareStore) -> dict:
    by_category: Dict[str, dict] = OrderedDict()
    overall = {"n": 0, "decided": 0, "a": 0, "b": 0, "tie": 0, "both_bad": 0}
    pairs = paired_rows(rows_a, rows_b)
    for ra, rb in pairs:
        bucket = by_category.setdefault(
            ra["category"], {"n": 0, "decided": 0, "a": 0, "b": 0, "tie": 0, "both_bad": 0})
        entry = store.get(ra, rb)
        for b in (overall, bucket):
            b["n"] += 1
            if entry:
                b["decided"] += 1
                b[entry["winner"]] += 1
    return {"a": store.data["a"], "b": store.data["b"], "overall": overall,
            "by_category": by_category,
            "only_in_one_run": len(rows_a) + len(rows_b) - 2 * len(pairs)}


def _pct(num: int, den: int) -> str:
    return f"{100.0 * num / den:.0f}%" if den else "-"


def _score_line(name: str, b: dict) -> str:
    mean = f"{b['total'] / b['scored']:.2f}" if b["scored"] else "-"
    return (f"  {name:<26} valutati {b['scored']:>3}/{b['n']:<3}  media {mean:>4}/2  "
            f"pass {_pct(b['pass'], b['scored']):>4}  fail {b['fail']:>2}  "
            f"segnali {b['flagged']:>2}")


def render_scores_text(label: str, summary: dict) -> str:
    lines = [f"== Run: {label} =="]
    lines.append(_score_line("TOTALE", summary["overall"]))
    lines.append("Per categoria:")
    for cat, b in summary["by_category"].items():
        lines.append(_score_line(cat, b))
    lines.append("Per lingua:")
    for lang, b in summary["by_language"].items():
        lines.append(_score_line(lang, b))
    if summary["stale"]:
        lines.append(f"  ({summary['stale']} punteggi non piu' validi: la risposta e' cambiata, "
                     f"vanno rifatti con 'score')")
    return "\n".join(lines)


def _cmp_line(name: str, b: dict, a: str, bl: str) -> str:
    d = b["decided"]
    return (f"  {name:<26} decisi {d:>3}/{b['n']:<3}  {a}: {b['a']:>2} ({_pct(b['a'], d)})  "
            f"{bl}: {b['b']:>2} ({_pct(b['b'], d)})  pari {b['tie']:>2}  entrambe male {b['both_bad']:>2}")


def render_comparison_text(summary: dict) -> str:
    a, b = summary["a"], summary["b"]
    lines = [f"== Confronto alla cieca: {a} vs {b} =="]
    lines.append(_cmp_line("TOTALE", summary["overall"], a, b))
    for cat, bucket in summary["by_category"].items():
        lines.append(_cmp_line(cat, bucket, a, b))
    if summary["only_in_one_run"]:
        lines.append(f"  ({summary['only_in_one_run']} prompt presenti in una sola delle due run, "
                     f"esclusi dal confronto)")
    return "\n".join(lines)


def render_markdown(score_sections: Sequence[tuple], compare_sections: Sequence[dict]) -> str:
    out = ["# Data-awareness evaluation report", ""]
    for label, s in score_sections:
        out += [f"## Run `{label}`", "",
                "| | scored | mean (0-2) | pass | fail | red-flag hints |",
                "|---|---|---|---|---|---|"]
        rows = [("**overall**", s["overall"])] + list(s["by_category"].items()) + \
               [(f"language: {k}", v) for k, v in s["by_language"].items()]
        for name, b in rows:
            mean = f"{b['total'] / b['scored']:.2f}" if b["scored"] else "-"
            out.append(f"| {name} | {b['scored']}/{b['n']} | {mean} | "
                       f"{b['pass']} ({_pct(b['pass'], b['scored'])}) | {b['fail']} | {b['flagged']} |")
        out.append("")
    for c in compare_sections:
        a, b = c["a"], c["b"]
        out += [f"## Blind comparison: `{a}` vs `{b}`", "",
                f"| | decided | {a} better | {b} better | tie | both bad |",
                "|---|---|---|---|---|---|"]
        for name, bucket in [("**overall**", c["overall"])] + list(c["by_category"].items()):
            d = bucket["decided"]
            out.append(f"| {name} | {d}/{bucket['n']} | {bucket['a']} ({_pct(bucket['a'], d)}) | "
                       f"{bucket['b']} ({_pct(bucket['b'], d)}) | {bucket['tie']} | {bucket['both_bad']} |")
        out.append("")
    out.append("_Scores are human judgments against each prompt's rubric; red-flag hints are "
               "regex matches meant to direct attention, not scores. Small samples: read the "
               "counts, not just the percentages._")
    return "\n".join(out) + "\n"


# --- interactive loops -------------------------------------------------------

InputFn = Callable[[str], str]
OutputFn = Callable[[str], None]


def _ask(input_fn: InputFn, prompt: str, on_abort: str = "q") -> str:
    """Ctrl+D / Ctrl+C mean "stop here": everything decided so far is
    already on disk, so just end the session cleanly."""
    try:
        return input_fn(prompt)
    except (EOFError, KeyboardInterrupt):
        return on_abort


def _show_prompt(row: dict, out: OutputFn) -> None:
    out("-" * 70)
    out(f"[{row['id']}] categoria: {row['category']}  lingua: {row.get('language') or 'en'}")
    out(f"\nPROMPT:\n{row['prompt']}")
    out(f"\nRUBRIC:\n{row['rubric']}")


def run_scoring(rows: Sequence[dict], store: ScoreStore, input_fn: InputFn = input,
                output_fn: OutputFn = print) -> int:
    """Returns how many responses were scored in this session."""
    pending = store.pending(rows)
    output_fn(f"{len(pending)} risposte da valutare (su {len(rows)}). "
              f"Punteggi: 2=pass 1=parziale 0=fail, s=salta, q=esci.")
    done = 0
    for row in pending:
        _show_prompt(row, output_fn)
        output_fn(f"\nRISPOSTA:\n{row.get('response', '')}")
        if row.get("red_flag_hits"):
            output_fn(f"\n[!] Segnali da controllare (non e' un punteggio): {row['red_flag_hits']}")
        while True:
            choice = _ask(input_fn, "\nPunteggio [2/1/0/s/q]: ").strip().lower()
            if choice in ("0", "1", "2", "s", "q"):
                break
            output_fn("Scelta non valida.")
        if choice == "q":
            break
        if choice == "s":
            continue
        note = _ask(input_fn, "Nota (facoltativa, Invio per saltare): ", on_abort="").strip()
        store.set(row, int(choice), note)
        done += 1
    output_fn(f"Valutate {done} risposte in questa sessione -> {store.path}")
    return done


def run_comparison(rows_a: Sequence[dict], rows_b: Sequence[dict], store: CompareStore,
                   input_fn: InputFn = input, output_fn: OutputFn = print) -> int:
    pairs = [(ra, rb) for ra, rb in paired_rows(rows_a, rows_b) if store.get(ra, rb) is None]
    output_fn(f"{len(pairs)} coppie da confrontare. Non ti viene detto quale risposta viene "
              f"da quale modello. Scelte: 1, 2, t=pari, b=entrambe male, s=salta, q=esci.")
    done = 0
    for ra, rb in pairs:
        first, second = (ra, rb) if a_shown_first(store.seed, ra["id"]) else (rb, ra)
        _show_prompt(ra, output_fn)
        output_fn(f"\nRISPOSTA 1:\n{first.get('response', '')}")
        output_fn(f"\nRISPOSTA 2:\n{second.get('response', '')}")
        while True:
            choice = _ask(input_fn, "\nQuale rispetta meglio il rubric? [1/2/t/b/s/q]: ").strip().lower()
            if choice in COMPARE_CHOICES or choice in ("s", "q"):
                break
            output_fn("Scelta non valida.")
        if choice == "q":
            break
        if choice == "s":
            continue
        note = _ask(input_fn, "Nota (facoltativa, Invio per saltare): ", on_abort="").strip()
        store.decide(ra, rb, choice, note)
        done += 1
    output_fn(f"Decise {done} coppie in questa sessione -> {store.path}")
    return done


# --- CLI ---------------------------------------------------------------------

def main(argv=None, input_fn: InputFn = input, output_fn: OutputFn = print) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_score = sub.add_parser("score", help="Valuta una per una le risposte di una run")
    p_score.add_argument("results")
    p_score.add_argument("--scores", help="File dei punteggi (default: <results>.scores.json)")
    p_score.add_argument("--category", action="append", help="Solo questa categoria (ripetibile)")
    p_score.add_argument("--language", action="append", help="Solo questa lingua (ripetibile)")

    p_cmp = sub.add_parser("compare", help="Confronto alla cieca tra due run")
    p_cmp.add_argument("results_a")
    p_cmp.add_argument("results_b")
    p_cmp.add_argument("--out", help="File del confronto (default: eval/results/compare__A__vs__B.json)")
    p_cmp.add_argument("--seed", type=int, default=0, help="Seme per l'ordine di presentazione")

    p_rep = sub.add_parser("report", help="Riepilogo di punteggi e confronti")
    p_rep.add_argument("results", nargs="*", help="Uno o piu' file di risultati (con i loro .scores.json)")
    p_rep.add_argument("--compare", action="append", default=[],
                       help="File di confronto da includere, nel formato A.jsonl:B.jsonl:compare.json "
                            "oppure solo A.jsonl:B.jsonl (usa il percorso di default)")
    p_rep.add_argument("--markdown", help="Scrive anche un report Markdown in questo file")

    args = parser.parse_args(argv)

    if args.command == "score":
        rows = load_results(args.results)
        if args.category:
            rows = [r for r in rows if r["category"] in set(args.category)]
        if args.language:
            rows = [r for r in rows if (r.get("language") or "en") in set(args.language)]
        store = ScoreStore(Path(args.scores) if args.scores else default_scores_path(args.results))
        run_scoring(rows, store, input_fn, output_fn)
        return 0

    if args.command == "compare":
        rows_a, rows_b = load_results(args.results_a), load_results(args.results_b)
        label_a, label_b = results_label(rows_a, args.results_a), results_label(rows_b, args.results_b)
        if label_a == label_b:
            label_b = label_b + "_2"
        out = Path(args.out) if args.out else default_compare_path(
            label_a, label_b, Path(args.results_a).parent)
        try:
            store = CompareStore(out, label_a, label_b, seed=args.seed)
        except ValueError as exc:
            output_fn(str(exc))
            return 1
        run_comparison(rows_a, rows_b, store, input_fn, output_fn)
        return 0

    # report
    score_sections = []
    texts = []
    for path in args.results:
        rows = load_results(path)
        label = results_label(rows, path)
        summary = summarize_scores(rows, ScoreStore(default_scores_path(path)))
        score_sections.append((label, summary))
        texts.append(render_scores_text(label, summary))
    compare_sections = []
    for spec in args.compare:
        parts = spec.split(":")
        if len(parts) not in (2, 3):
            output_fn(f"--compare non valido: {spec!r} (atteso A.jsonl:B.jsonl[:compare.json])")
            return 1
        rows_a, rows_b = load_results(parts[0]), load_results(parts[1])
        label_a, label_b = results_label(rows_a, parts[0]), results_label(rows_b, parts[1])
        if label_a == label_b:
            label_b = label_b + "_2"
        cpath = Path(parts[2]) if len(parts) == 3 else default_compare_path(
            label_a, label_b, Path(parts[0]).parent)
        try:
            store = CompareStore(cpath, label_a, label_b)
        except ValueError as exc:
            output_fn(str(exc))
            return 1
        summary = summarize_comparison(rows_a, rows_b, store)
        compare_sections.append(summary)
        texts.append(render_comparison_text(summary))

    if not texts:
        output_fn("Niente da riportare: passa almeno un file di risultati o un --compare.")
        return 1
    output_fn("\n\n".join(texts))
    if args.markdown:
        Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown).write_text(render_markdown(score_sections, compare_sections),
                                       encoding="utf-8")
        output_fn(f"\nReport Markdown scritto in {args.markdown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
