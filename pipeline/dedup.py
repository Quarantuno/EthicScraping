"""Deduplicazione dei contenuti: hash esatto + near-duplicate via SimHash.

L'hash esatto (sha256 su testo normalizzato) cattura pagine identiche o
che differiscono solo per spazi/maiuscole. Il SimHash cattura pagine
quasi identiche (stessa notizia ripubblicata con piccole modifiche,
mirror, paginazione che ripete il corpo dell'articolo, ecc.) confrontando
la distanza di Hamming tra le impronte a 64 bit.

Indice LSH a bande: il controllo near-duplicate non confronta piu' ogni
nuovo documento con tutti i SimHash visti finora (O(n) per documento).
L'impronta a `bits` bit viene divisa in `num_bands` bande; due impronte
identiche in almeno una banda sono candidate, e solo per queste si calcola
la vera distanza di Hamming. Per il principio dei cassetti, se due
impronte differiscono in al piu' `threshold` bit e il numero di bande e'
maggiore di `threshold`, almeno una banda coincide esattamente -- quindi
nessun near-duplicate reale (entro la soglia) viene perso rispetto al
confronto lineare originale, a patto che `num_bands > threshold`
(garantito automaticamente da `_choose_num_bands`). In pratica il costo
per documento scende da O(n) a O(candidati nelle bande), che per dataset
di milioni di pagine con pochi duplicati resta piccolo.
"""
from __future__ import annotations

import hashlib
import re
from typing import Dict, List, Optional, Set

TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)


def normalize_for_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    return normalized


def content_hash(text: str) -> str:
    normalized = normalize_for_hash(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _tokenize(text: str) -> List[str]:
    return TOKEN_PATTERN.findall(text.lower())


def _shingles(tokens: List[str], size: int = 4) -> List[str]:
    """n-grammi di parole contigue: catturano meglio la struttura di una
    frase rispetto alle singole parole (bag-of-words puro)."""
    if not tokens:
        return []
    if len(tokens) < size:
        return [" ".join(tokens)]
    return [" ".join(tokens[i:i + size]) for i in range(len(tokens) - size + 1)]


def _hash_token(token: str, bits: int) -> int:
    digest = hashlib.sha1(token.encode("utf-8")).digest()
    return int.from_bytes(digest[: bits // 8], "big")


def simhash(text: str, bits: int = 64, shingle_size: int = 4) -> int:
    """Impronta a `bits` bit: documenti simili producono impronte con
    poca distanza di Hamming tra loro."""
    shingles = _shingles(_tokenize(text), shingle_size)
    if not shingles:
        return 0

    weights = [0] * bits
    for shingle in shingles:
        h = _hash_token(shingle, bits)
        for i in range(bits):
            weights[i] += 1 if (h >> i) & 1 else -1

    fingerprint = 0
    for i in range(bits):
        if weights[i] > 0:
            fingerprint |= (1 << i)
    return fingerprint


def hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _choose_num_bands(bits: int, threshold: int) -> int:
    """Numero di bande piu' piccolo (quindi banda piu' larga possibile,
    per ridurre le collisioni spurie) che divide `bits` esattamente ed e'
    maggiore di `threshold`, cosi' da garantire per il principio dei
    cassetti che nessun near-duplicate entro `threshold` bit venga perso.
    """
    threshold = max(threshold, 0)
    for k in range(threshold + 1, bits + 1):
        if bits % k == 0:
            return k
    return bits  # fallback estremo: una banda per bit, sempre corretto


class _SimHashLSHIndex:
    """Indice a bande (locality-sensitive hashing) per cercare SimHash
    entro una distanza di Hamming massima senza scandire ogni impronta
    vista finora."""

    def __init__(self, bits: int, threshold: int):
        self.bits = bits
        self.threshold = threshold
        self.num_bands = _choose_num_bands(bits, threshold)
        self.band_bits = bits // self.num_bands
        self._buckets: List[Dict[int, Set[int]]] = [dict() for _ in range(self.num_bands)]

    def _band_value(self, fingerprint: int, band_index: int) -> int:
        shift = band_index * self.band_bits
        mask = (1 << self.band_bits) - 1
        return (fingerprint >> shift) & mask

    def find_within_threshold(self, fingerprint: int) -> bool:
        """True se esiste gia' un'impronta indicizzata a distanza di
        Hamming <= self.threshold da `fingerprint`."""
        candidates: Set[int] = set()
        for band_index in range(self.num_bands):
            bucket = self._buckets[band_index].get(self._band_value(fingerprint, band_index))
            if bucket:
                candidates.update(bucket)
        return any(hamming_distance(fingerprint, candidate) <= self.threshold
                    for candidate in candidates)

    def add(self, fingerprint: int) -> None:
        for band_index in range(self.num_bands):
            bv = self._band_value(fingerprint, band_index)
            self._buckets[band_index].setdefault(bv, set()).add(fingerprint)


class Deduplicator:
    def __init__(self, near_duplicate_threshold: Optional[int] = 8,
                 simhash_bits: int = 64):
        """
        near_duplicate_threshold: distanza di Hamming massima (su
            `simhash_bits` bit) perche' due documenti siano considerati
            quasi-duplicati. None disattiva il controllo fuzzy e lascia
            solo la deduplica esatta. Il default (8) e' calibrato su testi
            "da pagina web" di almeno un centinaio di parole: su testi molto
            corti una singola parola cambiata puo' spostare parecchi bit,
            quindi con `min_word_count` basso conviene alzare la soglia o
            disattivarla.
        """
        self._seen_hashes: Set[str] = set()
        self.near_duplicate_threshold = near_duplicate_threshold
        self.simhash_bits = simhash_bits
        self._lsh_index: Optional[_SimHashLSHIndex] = (
            _SimHashLSHIndex(simhash_bits, near_duplicate_threshold)
            if near_duplicate_threshold is not None else None
        )

    def is_duplicate(self, text: str) -> bool:
        h = content_hash(text)
        if h in self._seen_hashes:
            return True

        if self._lsh_index is not None:
            sh = simhash(text, bits=self.simhash_bits)
            if self._lsh_index.find_within_threshold(sh):
                self._seen_hashes.add(h)
                return True
            self._lsh_index.add(sh)

        self._seen_hashes.add(h)
        return False
