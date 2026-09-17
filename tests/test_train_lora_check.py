"""Test di training/train_lora.py::validate_config, che deliberatamente
non importa torch/transformers/peft (non installati in questo ambiente
di sviluppo): verifichiamo solo la validazione di config e percorsi, che
e' la parte eseguibile senza le dipendenze pesanti."""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "training"))

from train_lora import validate_config  # noqa: E402


class TestValidateConfig(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.train_file = os.path.join(self.tmpdir, "train.jsonl")
        with open(self.train_file, "w", encoding="utf-8") as f:
            f.write('{"text": "esempio"}\n')

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _base_config(self):
        return {
            "base_model": "Qwen/Qwen2.5-0.5B",
            "data": {"train_file": self.train_file},
            "lora": {"target_modules": ["q_proj", "v_proj"]},
            "training": {"output_dir": os.path.join(self.tmpdir, "out")},
        }

    def test_valid_config_has_no_problems(self):
        self.assertEqual(validate_config(self._base_config()), [])

    def test_missing_base_model_is_flagged(self):
        cfg = self._base_config()
        del cfg["base_model"]
        problems = validate_config(cfg)
        self.assertTrue(any("base_model" in p for p in problems))

    def test_missing_train_file_is_flagged(self):
        cfg = self._base_config()
        cfg["data"]["train_file"] = os.path.join(self.tmpdir, "non-esiste.jsonl")
        problems = validate_config(cfg)
        self.assertTrue(any("non esiste" in p for p in problems))

    def test_missing_target_modules_is_flagged(self):
        cfg = self._base_config()
        cfg["lora"]["target_modules"] = []
        problems = validate_config(cfg)
        self.assertTrue(any("target_modules" in p for p in problems))

    def test_missing_output_dir_is_flagged(self):
        cfg = self._base_config()
        del cfg["training"]["output_dir"]
        problems = validate_config(cfg)
        self.assertTrue(any("output_dir" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
