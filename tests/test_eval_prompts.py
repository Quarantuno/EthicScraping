"""Test di eval/run_eval.py: solo la validazione strutturale dei prompt
(load_prompts/validate_prompts), senza toccare torch/transformers/peft
-- coerente con lo stesso pattern usato per training/train_lora.py."""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eval"))

from run_eval import KNOWN_CATEGORIES, load_prompts, validate_prompts  # noqa: E402


class TestPromptsFile(unittest.TestCase):
    """Verifica il file eval/prompts.jsonl vero e proprio: se qualcuno lo
    modifica introducendo un id duplicato o un campo mancante, questo
    test deve accorgersene."""

    def test_real_prompts_file_is_valid(self):
        path = os.path.join(ROOT, "eval", "prompts.jsonl")
        prompts = load_prompts(path)
        self.assertGreater(len(prompts), 0)
        self.assertEqual(validate_prompts(prompts), [])

    def test_real_prompts_cover_every_known_category_in_both_languages(self):
        prompts = load_prompts(os.path.join(ROOT, "eval", "prompts.jsonl"))
        for lang in ("en", "it"):
            categories = {p["category"] for p in prompts if p.get("language", "en") == lang}
            self.assertEqual(categories, KNOWN_CATEGORIES, lang)

    def test_every_italian_prompt_is_a_variant_of_an_english_one_and_vice_versa(self):
        prompts = load_prompts(os.path.join(ROOT, "eval", "prompts.jsonl"))
        english = {p["id"]: p for p in prompts if p.get("language", "en") == "en"}
        italian = [p for p in prompts if p.get("language") == "it"]
        variants = {p["variant_of"] for p in italian}
        self.assertEqual(variants, set(english))
        for p in italian:
            self.assertEqual(p["category"], english[p["variant_of"]]["category"], p["id"])

    def test_over_refusal_prompts_flag_refusals(self):
        # helpfulness_calibration only works as a guard against a
        # "refuse everything" fine-tune if refusals get flagged.
        prompts = load_prompts(os.path.join(ROOT, "eval", "prompts.jsonl"))
        for p in prompts:
            if p["category"] == "helpfulness_calibration":
                self.assertTrue(p.get("red_flags"), p["id"])


class TestValidatePrompts(unittest.TestCase):
    def _valid_prompt(self, id_="p1"):
        return {"id": id_, "category": "privacy", "prompt": "Domanda?", "rubric": "Criterio."}

    def test_valid_list_has_no_problems(self):
        self.assertEqual(validate_prompts([self._valid_prompt()]), [])

    def test_empty_list_is_flagged(self):
        problems = validate_prompts([])
        self.assertTrue(any("vuoto" in p for p in problems))

    def test_missing_field_is_flagged(self):
        prompt = self._valid_prompt()
        del prompt["rubric"]
        problems = validate_prompts([prompt])
        self.assertTrue(any("rubric" in p for p in problems))

    def test_empty_prompt_text_is_flagged(self):
        prompt = self._valid_prompt()
        prompt["prompt"] = "   "
        problems = validate_prompts([prompt])
        self.assertTrue(any("vuoto" in p for p in problems))

    def test_duplicate_id_is_flagged(self):
        problems = validate_prompts([self._valid_prompt("dup"), self._valid_prompt("dup")])
        self.assertTrue(any("duplicato" in p for p in problems))


class TestLoadPrompts(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_skips_blank_lines(self):
        path = os.path.join(self.tmpdir, "p.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"id": "a", "category": "x", "prompt": "p", "rubric": "r"}\n')
            f.write("\n")
            f.write('{"id": "b", "category": "x", "prompt": "p2", "rubric": "r2"}\n')
        prompts = load_prompts(path)
        self.assertEqual(len(prompts), 2)


if __name__ == "__main__":
    unittest.main()
