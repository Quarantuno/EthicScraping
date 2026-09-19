"""Euristiche leggere, senza dipendenze esterne, per stimare quanto e'
"rumoroso" o degenerato il testo estratto da una pagina -- non un
giudizio sulla veridicita', l'imparzialita' o la qualita' della
scrittura (quello resta compito della revisione umana in
pipeline/review.py), ma un segnale a basso costo per scartare pagine che
sono per lo piu' menu di navigazione ripetuti, testo con codifica
rovinata, o "parole" anomale (URL lunghissimi, blob base64, codice
minificato sfuggito all'estrazione del testo principale).

Le metriche sono ispirate ai filtri di "qualita'" usati per ripulire
grandi corpus scrappati dal web (es. le euristiche di C4/Gopher),
semplificate e reimplementate qui in stdlib per evitare una dipendenza
pesante.
"""
from __future__ import annotations

import re
from typing import Optional

# Qualunque lettera, in qualunque alfabeto/script (non solo ASCII).
_ALPHA_RE = re.compile(r"[^\W\d_]", re.UNICODE)
_WORD_RE = re.compile(r"\S+")


def compute_quality_metrics(text: Optional[str]) -> dict:
    """Ritorna le singole metriche piu' un `quality_score` unico (0-1,
    piu' alto e' meglio) utile per ordinare/riportare, ma NON pensato
    come soglia di scarto diretta -- per quello vedi
    passes_quality_filter(), che usa le singole metriche."""
    text = text or ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    words = _WORD_RE.findall(text)

    total_chars = len(text)
    alpha_chars = len(_ALPHA_RE.findall(text))
    alpha_ratio = (alpha_chars / total_chars) if total_chars else 0.0

    unique_line_ratio = (len(set(lines)) / len(lines)) if lines else 1.0

    avg_word_length = (sum(len(w) for w in words) / len(words)) if words else 0.0
    # "Parole" assurdamente lunghe (codice minificato, base64, URL non
    # ripulite): penalizza la loro frequenza senza punire troppo parole
    # normali ma lunghe (composti tedeschi/italiani, termini tecnici).
    long_word_ratio = (sum(1 for w in words if len(w) > 40) / len(words)) if words else 0.0

    metrics = {
        "alpha_ratio": round(alpha_ratio, 3),
        "unique_line_ratio": round(unique_line_ratio, 3),
        "avg_word_length": round(avg_word_length, 2),
        "long_word_ratio": round(long_word_ratio, 3),
    }

    alpha_goodness = min(1.0, alpha_ratio / 0.6)
    line_goodness = unique_line_ratio
    word_len_goodness = 1.0 if 3.0 <= avg_word_length <= 12.0 else 0.5
    long_word_goodness = max(0.0, 1.0 - long_word_ratio * 5)

    metrics["quality_score"] = round(
        (alpha_goodness + line_goodness + word_len_goodness + long_word_goodness) / 4, 3
    )
    return metrics


def passes_quality_filter(metrics: dict, min_alpha_ratio: float = 0.5,
                           min_unique_line_ratio: float = 0.4,
                           max_long_word_ratio: float = 0.05) -> bool:
    """Soglia netta sulle singole metriche (non sul quality_score
    aggregato, pensato per il reporting piu' che come cutoff diretto)."""
    if metrics["alpha_ratio"] < min_alpha_ratio:
        return False
    if metrics["unique_line_ratio"] < min_unique_line_ratio:
        return False
    if metrics["long_word_ratio"] > max_long_word_ratio:
        return False
    return True
