"""Deduplicazione dei contenuti: hash esatto + near-duplicate via SimHash.

L'hash esatto (sha256 su testo normalizzato) cattura pagine identiche o
che differiscono solo per spazi/maiuscole. Il SimHash cattura pagine
quasi identiche (stessa notizia ripubblicata con piccole modifiche,
mirror, paginazione che ripete il corpo dell'articolo, ecc.) confrontando
la distanza di Hamming tra le impronte a 64 bit.

Nota di scala: il controllo near-duplicate confronta ogni nuovo documento
con tutti i SimHash visti finora (O(n) per documento). Va benissimo per
dataset di migliaia di pagine; per milioni di pagine servirebbe un indice
LSH (bucket per prefisso di bit) -- non incluso qui per restare semplice
nella fase 1.
"""
from __future__ import annotations

import hashlib
import re
from typing import List, Optional, Set

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
        self._seen_simhashes: List[int] = []
        self.near_duplicate_threshold = near_duplicate_threshold
        self.simhash_bits = simhash_bits

    def is_duplicate(self, text: str) -> bool:
        h = content_hash(text)
        if h in self._seen_hashes:
            return True

        if self.near_duplicate_threshold is not None:
            sh = simhash(text, bits=self.simhash_bits)
            for seen_sh in self._seen_simhashes:
                if hamming_distance(sh, seen_sh) <= self.near_duplicate_threshold:
                    self._seen_hashes.add(h)
                    return True
            self._seen_simhashes.append(sh)

        self._seen_hashes.add(h)
        return False
