"""Test per pipeline.dedup: hash esatto, SimHash e l'indice LSH a bande
che lo sostituisce nel confronto near-duplicate.
"""
import random
import time
import unittest

from pipeline.dedup import (
    Deduplicator,
    _choose_num_bands,
    _SimHashLSHIndex,
    content_hash,
    hamming_distance,
    simhash,
)


def _brute_force_within_threshold(fingerprint, seen, threshold):
    return any(hamming_distance(fingerprint, s) <= threshold for s in seen)


class TestChooseNumBands(unittest.TestCase):
    def test_num_bands_exceeds_threshold(self):
        for threshold in (0, 1, 4, 8, 15, 31):
            k = _choose_num_bands(64, threshold)
            self.assertGreater(k, threshold)
            self.assertEqual(64 % k, 0)

    def test_zero_threshold_uses_single_band(self):
        # soglia 0 = solo match esatto del simhash: una banda sola
        # (l'intera impronta) basta e riduce al minimo le collisioni spurie.
        self.assertEqual(_choose_num_bands(64, 0), 1)

    def test_default_threshold_eight_gives_sixteen_bands(self):
        self.assertEqual(_choose_num_bands(64, 8), 16)


class TestSimHashLSHIndex(unittest.TestCase):
    def test_finds_exact_match(self):
        index = _SimHashLSHIndex(bits=64, threshold=8)
        index.add(12345)
        self.assertTrue(index.find_within_threshold(12345))

    def test_finds_fingerprint_within_threshold(self):
        index = _SimHashLSHIndex(bits=64, threshold=8)
        fp = 0b1010101010101010101010101010101010101010101010101010101010101
        index.add(fp)
        # capovolge 5 bit bassi: distanza di Hamming = 5 <= soglia 8
        near = fp ^ 0b11111
        self.assertTrue(index.find_within_threshold(near))

    def test_does_not_find_fingerprint_beyond_threshold(self):
        index = _SimHashLSHIndex(bits=64, threshold=4)
        fp = 0
        index.add(fp)
        far = (1 << 10) - 1  # 10 bit accesi, distanza 10 > soglia 4
        self.assertFalse(index.find_within_threshold(far))

    def test_matches_brute_force_on_random_fingerprints(self):
        # Verifica di correttezza: l'indice a bande deve dare *esattamente*
        # lo stesso responso del confronto lineare originale (nessun falso
        # negativo, nessun falso positivo) su un campione casuale.
        rng = random.Random(42)
        threshold = 8
        index = _SimHashLSHIndex(bits=64, threshold=threshold)
        seen = []
        mismatches = 0
        for _ in range(300):
            fp = rng.getrandbits(64)
            expected = _brute_force_within_threshold(fp, seen, threshold)
            actual = index.find_within_threshold(fp)
            if expected != actual:
                mismatches += 1
            if not expected:
                index.add(fp)
                seen.append(fp)
        self.assertEqual(mismatches, 0)


class TestDeduplicatorScale(unittest.TestCase):
    def test_lookup_stays_fast_as_corpus_grows(self):
        # Non e' un benchmark rigoroso, ma una prova di non-regressione:
        # con l'indice LSH il costo per documento non deve crescere
        # linearmente con la dimensione del corpus gia' visto. Genera
        # ~3000 testi unici e verifica che l'ultimo batch non richieda un
        # tempo per elemento sensibilmente maggiore del primo batch.
        rng = random.Random(7)

        def random_text(n_words=150):
            words = [f"parola{rng.randint(0, 5000)}" for _ in range(n_words)]
            return " ".join(words)

        dedup = Deduplicator(near_duplicate_threshold=8)
        texts = [random_text() for _ in range(3000)]

        start = time.perf_counter()
        for t in texts[:500]:
            dedup.is_duplicate(t)
        first_batch = time.perf_counter() - start

        start = time.perf_counter()
        for t in texts[2500:]:
            dedup.is_duplicate(t)
        last_batch = time.perf_counter() - start

        # Con una scansione O(n) l'ultimo batch (corpus 5x piu' grande alle
        # spalle) sarebbe nettamente piu' lento del primo; con l'indice a
        # bande deve restare nello stesso ordine di grandezza.
        self.assertLess(last_batch, first_batch * 4 + 0.5)


class TestDeduplicatorBehaviorUnchanged(unittest.TestCase):
    """Le stesse garanzie comportamentali della versione precedente
    (confronto lineare), ora servite dall'indice LSH."""

    _LONG_ORIGINAL = (
        "Questo e' un articolo abbastanza lungo che parla di un argomento "
        "qualsiasi e serve solo per avere abbastanza shingle di quattro "
        "parole da produrre un simhash stabile e confrontabile con una "
        "versione leggermente modificata dello stesso testo di prova."
    )

    def test_exact_duplicate_detected(self):
        dedup = Deduplicator()
        self.assertFalse(dedup.is_duplicate("Testo identico"))
        self.assertTrue(dedup.is_duplicate("testo   identico"))
        self.assertFalse(dedup.is_duplicate("Testo diverso"))

    def test_near_duplicate_detected_with_fuzzy_matching(self):
        near_dup = self._LONG_ORIGINAL.replace("qualsiasi", "specifico")
        unrelated = "Contenuto completamente diverso su un altro tema, " * 5

        dedup = Deduplicator(near_duplicate_threshold=8)
        self.assertFalse(dedup.is_duplicate(self._LONG_ORIGINAL))
        self.assertTrue(dedup.is_duplicate(near_dup))
        self.assertFalse(dedup.is_duplicate(unrelated))

    def test_fuzzy_matching_can_be_disabled(self):
        near_dup = self._LONG_ORIGINAL.replace("qualsiasi", "specifico")
        dedup = Deduplicator(near_duplicate_threshold=None)
        self.assertFalse(dedup.is_duplicate(self._LONG_ORIGINAL))
        self.assertFalse(dedup.is_duplicate(near_dup))  # solo hash esatto


class TestContentHashAndSimhash(unittest.TestCase):
    def test_content_hash_ignores_case_and_whitespace(self):
        self.assertEqual(content_hash("Ciao  Mondo"), content_hash("ciao mondo"))

    def test_simhash_empty_text_is_zero(self):
        self.assertEqual(simhash(""), 0)


if __name__ == "__main__":
    unittest.main()
