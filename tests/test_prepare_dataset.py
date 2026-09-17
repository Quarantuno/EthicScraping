"""Test di training/prepare_dataset.py: solo stdlib, nessuna dipendenza
pesante richiesta (torch/transformers non servono per preparare i dati)."""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "training"))

from prepare_dataset import (  # noqa: E402
    build_examples,
    chunk_text,
    load_records,
    split_examples,
    write_jsonl,
)


class TestChunkText(unittest.TestCase):
    def test_short_text_is_a_single_chunk(self):
        text = "poche parole qui"
        self.assertEqual(chunk_text(text, max_words=100), [text])

    def test_long_text_is_split_into_multiple_chunks(self):
        words = [f"parola{i}" for i in range(1000)]
        text = " ".join(words)
        chunks = chunk_text(text, max_words=300)
        self.assertEqual(len(chunks), 4)  # 300+300+300+100
        # Nessuna parola persa o duplicata (senza overlap)
        rebuilt = " ".join(chunks).split()
        self.assertEqual(rebuilt, words)

    def test_empty_text_yields_no_chunks(self):
        self.assertEqual(chunk_text(""), [])


class TestBuildExamples(unittest.TestCase):
    def test_skips_empty_text_and_keeps_provenance(self):
        records = [
            {"id": "a", "url": "https://x.it/a", "license": "CC BY-SA 4.0", "text": "testo valido qui"},
            {"id": "b", "url": "https://x.it/b", "text": ""},
        ]
        examples = build_examples(records, max_words=100)
        self.assertEqual(len(examples), 1)
        self.assertEqual(examples[0]["source_url"], "https://x.it/a")
        self.assertEqual(examples[0]["license"], "CC BY-SA 4.0")


class TestSplitExamples(unittest.TestCase):
    def test_split_is_deterministic_for_a_given_seed(self):
        examples = [{"text": f"esempio {i}"} for i in range(100)]
        train1, val1 = split_examples(examples, val_ratio=0.1, seed=7)
        train2, val2 = split_examples(examples, val_ratio=0.1, seed=7)
        self.assertEqual(train1, train2)
        self.assertEqual(val1, val2)
        self.assertEqual(len(val1), 10)
        self.assertEqual(len(train1), 90)

    def test_small_dataset_skips_validation_split(self):
        examples = [{"text": "solo un esempio"}]
        train, val = split_examples(examples, val_ratio=0.1)
        self.assertEqual(train, examples)
        self.assertEqual(val, [])

    def test_rejects_invalid_ratio(self):
        with self.assertRaises(ValueError):
            split_examples([{"text": "x"}], val_ratio=1.5)


class TestJsonlRoundtrip(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_write_then_load_records_roundtrip(self):
        examples = [{"text": "uno"}, {"text": "due"}]
        path = os.path.join(self.tmpdir, "sub", "out.jsonl")
        write_jsonl(path, examples)
        loaded = load_records(path)
        self.assertEqual(loaded, examples)


if __name__ == "__main__":
    unittest.main()
