"""Tests for eval/score.py: resumable human scoring, the blind A/B
comparison (including that what the reviewer typed for the *displayed*
order is resolved to the right run), and the report numbers. The
interactive loops are driven with scripted input, no terminal needed."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "eval"))

import score  # noqa: E402
from score import (  # noqa: E402
    CompareStore, ScoreStore, a_shown_first, default_scores_path, paired_rows,
    render_markdown, run_comparison, run_scoring, summarize_comparison, summarize_scores,
)


def _row(id_, category="privacy", language="en", response="risposta", flags=None, label="base"):
    return {"id": id_, "category": category, "language": language, "prompt": f"p {id_}",
            "rubric": "r", "response": response, "red_flag_hits": flags or [],
            "run": {"label": label}}


def _scripted(*answers):
    it = iter(answers)
    return lambda prompt="": next(it)


def _silent(_msg):
    pass


class TmpDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_results(self, name, rows):
        path = self.tmp / name
        with path.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        return path


class TestScoreStore(TmpDirTest):
    def test_set_get_and_persist(self):
        row = _row("a")
        store = ScoreStore(self.tmp / "s.json")
        store.set(row, 2, "ok")
        reloaded = ScoreStore(self.tmp / "s.json")
        self.assertEqual(reloaded.get(row)["score"], 2)
        self.assertEqual(reloaded.get(row)["note"], "ok")
        self.assertEqual(reloaded.pending([row, _row("b")]), [_row("b")])

    def test_score_is_invalidated_when_the_response_changes(self):
        store = ScoreStore(self.tmp / "s.json")
        store.set(_row("a", response="vecchia"), 0)
        new = _row("a", response="nuova")
        self.assertIsNone(store.get(new))
        self.assertTrue(store.is_stale(new))
        self.assertEqual(store.pending([new]), [new])

    def test_rejects_invalid_score(self):
        with self.assertRaises(ValueError):
            ScoreStore(self.tmp / "s.json").set(_row("a"), 3)

    def test_default_scores_path(self):
        self.assertEqual(default_scores_path("eval/results/base.jsonl").name, "base.scores.json")


class TestInteractiveScoring(TmpDirTest):
    def test_scores_skips_and_quits(self):
        rows = [_row("a"), _row("b"), _row("c"), _row("d")]
        store = ScoreStore(self.tmp / "s.json")
        # a: invalid then 2 + note; b: skip; c: 0, no note; d: quit
        done = run_scoring(rows, store, _scripted("9", "2", "bella", "s", "0", "", "q"), _silent)
        self.assertEqual(done, 2)
        self.assertEqual(store.get(rows[0])["score"], 2)
        self.assertEqual(store.get(rows[0])["note"], "bella")
        self.assertIsNone(store.get(rows[1]))
        self.assertEqual(store.get(rows[2])["score"], 0)
        self.assertIsNone(store.get(rows[3]))

    def test_end_of_input_ends_the_session_cleanly(self):
        def eof(_prompt=""):
            raise EOFError
        store = ScoreStore(self.tmp / "s.json")
        self.assertEqual(run_scoring([_row("a")], store, eof, _silent), 0)

    def test_resuming_only_asks_about_pending(self):
        rows = [_row("a"), _row("b")]
        store = ScoreStore(self.tmp / "s.json")
        store.set(rows[0], 1)
        shown = []
        run_scoring(rows, store, _scripted("2", ""), shown.append)
        self.assertTrue(any("[b]" in s for s in shown))
        self.assertFalse(any("[a]" in s for s in shown))

    def test_red_flag_hints_are_shown_as_hints(self):
        shown = []
        run_scoring([_row("a", flags=["proxy"])], ScoreStore(self.tmp / "s.json"),
                    _scripted("q"), shown.append)
        self.assertTrue(any("non e' un punteggio" in s and "proxy" in s for s in shown))


class TestBlindComparison(TmpDirTest):
    def test_display_order_is_deterministic_and_mixed(self):
        ids = [f"p{i}" for i in range(40)]
        first = [a_shown_first(0, i) for i in ids]
        self.assertEqual(first, [a_shown_first(0, i) for i in ids])
        self.assertIn(True, first)
        self.assertIn(False, first)
        self.assertNotEqual(first, [a_shown_first(1, i) for i in ids])

    def _find_ids(self, a_first_wanted):
        for i in range(100):
            if a_shown_first(0, f"p{i}") == a_first_wanted:
                return f"p{i}"
        raise AssertionError("no id found")

    def test_choice_is_resolved_against_display_order(self):
        store = CompareStore(self.tmp / "c.json", "base", "ft")
        id_a_first = self._find_ids(True)
        id_b_first = self._find_ids(False)
        ra, rb = _row(id_a_first, response="A"), _row(id_a_first, response="B", label="ft")
        self.assertEqual(store.decide(ra, rb, "1")["winner"], "a")
        ra2, rb2 = _row(id_b_first, response="A"), _row(id_b_first, response="B", label="ft")
        self.assertEqual(store.decide(ra2, rb2, "1")["winner"], "b")
        self.assertEqual(store.decide(ra2, rb2, "t")["winner"], "tie")
        self.assertEqual(store.decide(ra2, rb2, "b")["winner"], "both_bad")

    def test_interactive_comparison_hides_labels(self):
        rows_a = [_row("p1", response="risposta della base")]
        rows_b = [_row("p1", response="risposta del fine-tune", label="ft")]
        store = CompareStore(self.tmp / "c.json", "base", "ft")
        shown = []
        done = run_comparison(rows_a, rows_b, store, _scripted("x", "2", ""), shown.append)
        self.assertEqual(done, 1)
        text = "\n".join(shown)
        self.assertNotIn("base", text.replace("risposta della base", ""))
        self.assertNotIn("ft", text.replace("risposta del fine-tune", ""))
        expected = "a" if not a_shown_first(0, "p1") else "b"
        self.assertEqual(store.get(rows_a[0], rows_b[0])["winner"], expected)

    def test_decision_invalidated_if_either_response_changes(self):
        store = CompareStore(self.tmp / "c.json", "base", "ft")
        ra, rb = _row("p1", response="A"), _row("p1", response="B")
        store.decide(ra, rb, "t")
        self.assertIsNotNone(store.get(ra, rb))
        self.assertIsNone(store.get(ra, _row("p1", response="B2")))

    def test_compare_file_for_other_runs_is_refused(self):
        CompareStore(self.tmp / "c.json", "base", "ft").decide(_row("p"), _row("p"), "t")
        with self.assertRaises(ValueError):
            CompareStore(self.tmp / "c.json", "base", "other")

    def test_paired_rows_only_shared_ids(self):
        pairs = paired_rows([_row("a"), _row("b")], [_row("b"), _row("c")])
        self.assertEqual([(x["id"], y["id"]) for x, y in pairs], [("b", "b")])


class TestSummaries(TmpDirTest):
    def test_score_summary_per_category_and_language(self):
        rows = [_row("a", "privacy", "en"), _row("b", "privacy", "it", flags=["x"]),
                _row("c", "transparency", "en"), _row("d", "transparency", "it")]
        store = ScoreStore(self.tmp / "s.json")
        store.set(rows[0], 2)
        store.set(rows[1], 0)
        store.set(rows[2], 1)
        s = summarize_scores(rows, store)
        self.assertEqual(s["overall"]["n"], 4)
        self.assertEqual(s["overall"]["scored"], 3)
        self.assertEqual(s["overall"]["total"], 3)
        self.assertEqual(s["overall"]["pass"], 1)
        self.assertEqual(s["overall"]["fail"], 1)
        self.assertEqual(s["overall"]["flagged"], 1)
        self.assertEqual(s["by_category"]["privacy"]["total"], 2)
        self.assertEqual(s["by_language"]["it"]["scored"], 1)
        self.assertEqual(s["stale"], 0)

    def test_comparison_summary(self):
        rows_a = [_row("p1", "privacy"), _row("p2", "privacy"), _row("p3", "transparency"), _row("solo")]
        rows_b = [_row("p1", "privacy"), _row("p2", "privacy"), _row("p3", "transparency")]
        store = CompareStore(self.tmp / "c.json", "base", "ft")
        for ra, rb in paired_rows(rows_a, rows_b)[:2]:
            # choose whatever displayed option maps to "b" (the fine-tune)
            store.decide(ra, rb, "2" if a_shown_first(store.seed, ra["id"]) else "1")
        s = summarize_comparison(rows_a, rows_b, store)
        self.assertEqual(s["overall"]["n"], 3)
        self.assertEqual(s["overall"]["decided"], 2)
        self.assertEqual(s["overall"]["b"], 2)
        self.assertEqual(s["by_category"]["privacy"]["b"], 2)
        self.assertEqual(s["only_in_one_run"], 1)

    def test_markdown_renders(self):
        rows = [_row("a")]
        store = ScoreStore(self.tmp / "s.json")
        store.set(rows[0], 2)
        md = render_markdown([("base", summarize_scores(rows, store))], [])
        self.assertIn("## Run `base`", md)
        self.assertIn("| **overall** | 1/1 | 2.00 |", md)


class TestCli(TmpDirTest):
    def test_score_then_report(self):
        path = self.write_results("base.jsonl", [_row("a"), _row("b", "transparency")])
        out = []
        score.main(["score", str(path)], _scripted("2", "", "1", "nota"), out.append)
        self.assertTrue((self.tmp / "base.scores.json").exists())
        out.clear()
        md = self.tmp / "report.md"
        code = score.main(["report", str(path), "--markdown", str(md)], input, out.append)
        self.assertEqual(code, 0)
        self.assertIn("== Run: base ==", out[0])
        self.assertIn("valutati   2/2", out[0])
        self.assertTrue(md.exists())

    def test_compare_then_report(self):
        a = self.write_results("base.jsonl", [_row("p1")])
        b = self.write_results("ft.jsonl", [_row("p1", response="diversa", label="ft")])
        score.main(["compare", str(a), str(b)], _scripted("t", ""), _silent)
        self.assertTrue((self.tmp / "compare__base__vs__ft.json").exists())
        out = []
        code = score.main(["report", "--compare", f"{a}:{b}"], input, out.append)
        self.assertEqual(code, 0)
        self.assertIn("base vs ft", out[0])
        self.assertIn("pari  1", out[0])

    def test_report_with_nothing_fails(self):
        self.assertEqual(score.main(["report"], input, _silent), 1)


if __name__ == "__main__":
    unittest.main()
