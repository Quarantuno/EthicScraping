"""Tests for eval/run_eval.py beyond prompt-file validation: filters,
red-flag hints, chat-template rendering, the CLI's output handling, and
the generation loop itself -- the latter with tiny fake `torch` /
`transformers` modules injected into sys.modules, so the real loop runs
end to end without any heavy dependency or model download."""
import contextlib
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "eval"))

import run_eval  # noqa: E402
from run_eval import (  # noqa: E402
    build_result_row, filter_prompts, find_red_flags, prompt_set_fingerprint,
    render_input, validate_prompts,
)


def _p(id_, category="privacy", language=None, **extra):
    p = {"id": id_, "category": category, "prompt": f"domanda {id_}?", "rubric": "criterio"}
    if language:
        p["language"] = language
    p.update(extra)
    return p


class TestValidationRules(unittest.TestCase):
    def test_unknown_category(self):
        problems = validate_prompts([_p("a", category="vibes")])
        self.assertTrue(any("categoria sconosciuta" in p for p in problems))

    def test_unknown_language(self):
        self.assertTrue(any("lingua" in p for p in validate_prompts([_p("a", language="fr")])))

    def test_empty_rubric(self):
        prompt = _p("a")
        prompt["rubric"] = " "
        self.assertTrue(any("rubric" in p for p in validate_prompts([prompt])))

    def test_invalid_red_flag_regex(self):
        problems = validate_prompts([_p("a", red_flags=["(unclosed"])])
        self.assertTrue(any("red_flag non valida" in p for p in problems))

    def test_red_flags_must_be_a_list(self):
        problems = validate_prompts([_p("a", red_flags="proxy")])
        self.assertTrue(any("lista" in p for p in problems))

    def test_variant_of_must_exist(self):
        problems = validate_prompts([_p("a-it", language="it", variant_of="a")])
        self.assertTrue(any("variant_of" in p for p in problems))
        self.assertEqual(validate_prompts([_p("a"), _p("a-it", language="it", variant_of="a")]), [])


class TestFilters(unittest.TestCase):
    PROMPTS = [_p("1", "privacy"), _p("2", "privacy", "it"), _p("3", "transparency", "en")]

    def test_no_filters_keeps_everything(self):
        self.assertEqual(len(filter_prompts(self.PROMPTS)), 3)

    def test_missing_language_counts_as_english(self):
        self.assertEqual([p["id"] for p in filter_prompts(self.PROMPTS, languages=["en"])], ["1", "3"])

    def test_category_and_limit(self):
        self.assertEqual([p["id"] for p in filter_prompts(self.PROMPTS, categories=["privacy"], limit=1)],
                         ["1"])


class TestRedFlags(unittest.TestCase):
    def test_case_insensitive_matches_are_reported(self):
        hits = find_red_flags("Use ROTATING proxies and set robotstxt_obey = False",
                              [r"rotat\w*\s+proxies", r"ROBOTSTXT_OBEY\s*=\s*False", r"captcha"])
        self.assertEqual(hits, [r"rotat\w*\s+proxies", r"ROBOTSTXT_OBEY\s*=\s*False"])

    def test_nothing_to_check(self):
        self.assertEqual(find_red_flags("testo", None), [])
        self.assertEqual(find_red_flags("", ["x"]), [])

    def test_result_row_carries_hits_and_language(self):
        row = build_result_row(_p("a", red_flags=[r"I can'?t help"]), "Sorry, I can't help.", {"label": "x"})
        self.assertEqual(row["red_flag_hits"], [r"I can'?t help"])
        self.assertEqual(row["language"], "en")
        self.assertEqual(row["run"], {"label": "x"})


class TestFingerprint(unittest.TestCase):
    def test_order_independent_but_wording_sensitive(self):
        a, b = _p("1"), _p("2")
        self.assertEqual(prompt_set_fingerprint([a, b]), prompt_set_fingerprint([b, a]))
        changed = dict(a, prompt="altra domanda?")
        self.assertNotEqual(prompt_set_fingerprint([a, b]), prompt_set_fingerprint([changed, b]))


class ChatTokenizer:
    chat_template = "{{ messages }}"

    def __init__(self):
        self.calls = []

    def apply_chat_template(self, messages, tokenize, add_generation_prompt):
        self.calls.append((messages, tokenize, add_generation_prompt))
        return "<chat>" + "|".join(f"{m['role']}:{m['content']}" for m in messages) + "<assistant>"


class PlainTokenizer:
    chat_template = None


class TestRenderInput(unittest.TestCase):
    def test_uses_chat_template_when_available(self):
        tok = ChatTokenizer()
        text, templated = render_input(tok, "Ciao?", system_prompt="Sii onesto.")
        self.assertTrue(templated)
        self.assertEqual(text, "<chat>system:Sii onesto.|user:Ciao?<assistant>")
        self.assertEqual(tok.calls[0][1:], (False, True))

    def test_plain_prompt_without_template(self):
        self.assertEqual(render_input(PlainTokenizer(), "Ciao?"), ("Ciao?", False))

    def test_system_prompt_prepended_without_template(self):
        self.assertEqual(render_input(PlainTokenizer(), "Ciao?", system_prompt="S."), ("S.\n\nCiao?", False))

    def test_chat_template_can_be_disabled(self):
        self.assertEqual(render_input(ChatTokenizer(), "Ciao?", use_chat_template=False), ("Ciao?", False))


# --- fake torch / transformers for the generation loop -------------------

class _Ids(list):
    @property
    def shape(self):
        return (1, len(self[0]))


class _Encoding(dict):
    def to(self, device):
        self["device"] = device
        return self


class FakeTokenizer(ChatTokenizer):
    pad_token = None
    eos_token = "</s>"
    pad_token_id = 0
    seen_add_special = []

    @classmethod
    def from_pretrained(cls, name):
        return cls()

    def __call__(self, text, return_tensors, add_special_tokens):
        FakeTokenizer.seen_add_special.append(add_special_tokens)
        return _Encoding(input_ids=_Ids([list(range(len(text.split())))]))

    def decode(self, ids, skip_special_tokens):
        return "  risposta finta: non posso aiutarti con questo  "


class FakeModel:
    @classmethod
    def from_pretrained(cls, name):
        return cls()

    def to(self, device):
        self.device = device
        return self

    def eval(self):
        return self

    def generate(self, input_ids, max_new_tokens, do_sample, pad_token_id, device=None):
        assert do_sample is False
        return [input_ids[0] + [99, 98]]


def _fake_modules():
    torch = types.ModuleType("torch")
    torch.manual_seed = lambda s: None
    torch.no_grad = contextlib.nullcontext
    torch.cuda = types.SimpleNamespace(is_available=lambda: False)
    torch.backends = types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: False))
    transformers = types.ModuleType("transformers")
    transformers.AutoTokenizer = FakeTokenizer
    transformers.AutoModelForCausalLM = FakeModel
    peft = types.ModuleType("peft")
    peft.PeftModel = types.SimpleNamespace(from_pretrained=lambda model, path: model)
    return {"torch": torch, "transformers": transformers, "peft": peft}


class TestGenerationLoop(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        FakeTokenizer.seen_add_special = []

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_prompts(self, prompts):
        path = os.path.join(self.tmpdir, "prompts.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for p in prompts:
                f.write(json.dumps(p) + "\n")
        return path

    def test_end_to_end_with_fake_model(self):
        prompts_path = self._write_prompts([
            _p("help-01", "helpfulness_calibration", "it", red_flags=["non posso aiutar"]),
            _p("privacy-01", "privacy", "en"),
        ])
        out = os.path.join(self.tmpdir, "res", "base.jsonl")
        with patch.dict(sys.modules, _fake_modules()):
            code = run_eval.main(["--prompts", prompts_path, "--base-model", "fake/model",
                                  "--output", out, "--system-prompt", "S."])
        self.assertEqual(code, 0)
        rows = [json.loads(line) for line in open(out, encoding="utf-8")]
        self.assertEqual([r["id"] for r in rows], ["help-01", "privacy-01"])
        self.assertEqual(rows[0]["response"], "risposta finta: non posso aiutarti con questo")
        self.assertEqual(rows[0]["red_flag_hits"], ["non posso aiutar"])
        self.assertEqual(rows[1]["red_flag_hits"], [])
        run = rows[0]["run"]
        self.assertEqual(run["label"], "base")
        self.assertEqual(run["base_model"], "fake/model")
        self.assertEqual(run["device"], "cpu")
        self.assertTrue(run["chat_template"])
        self.assertEqual(run["system_prompt"], "S.")
        self.assertTrue(run["prompt_set"])
        # Templated input: no second BOS added by the tokenizer call.
        self.assertEqual(FakeTokenizer.seen_add_special, [False, False])

    def test_refuses_to_overwrite_existing_results(self):
        prompts_path = self._write_prompts([_p("a")])
        out = os.path.join(self.tmpdir, "base.jsonl")
        open(out, "w").close()
        with patch.dict(sys.modules, _fake_modules()):
            self.assertEqual(run_eval.main(["--prompts", prompts_path, "--base-model", "m",
                                            "--output", out]), 1)
            self.assertEqual(run_eval.main(["--prompts", prompts_path, "--base-model", "m",
                                            "--output", out, "--overwrite"]), 0)

    def test_default_output_path_uses_label(self):
        prompts_path = self._write_prompts([_p("a")])
        with patch.dict(sys.modules, _fake_modules()), \
                patch.object(run_eval, "DEFAULT_RESULTS_DIR", run_eval.Path(self.tmpdir)):
            self.assertEqual(run_eval.main(["--prompts", prompts_path, "--base-model", "m",
                                            "--adapter", "ad"]), 0)
            self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "finetuned.jsonl")))

    def test_filters_that_match_nothing_fail_cleanly(self):
        prompts_path = self._write_prompts([_p("a", language="en")])
        self.assertEqual(run_eval.main(["--prompts", prompts_path, "--base-model", "m",
                                        "--language", "it"]), 1)

    def test_check_does_not_need_heavy_dependencies(self):
        prompts_path = self._write_prompts([_p("a")])
        self.assertEqual(run_eval.main(["--prompts", prompts_path, "--check"]), 0)


if __name__ == "__main__":
    unittest.main()
