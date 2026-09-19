"""Rilevamento lingua (opzionale) del testo estratto da una pagina, per
poter filtrare il dataset su una o piu' lingue di destinazione.

Usa il pacchetto `langdetect`, che NON e' una dipendenza installata di
default (vedi requirements.txt), per restare nello spirito "nessuna
dipendenza pesante di default" del progetto. Se non e' installato, la
lingua rilevata e' sempre None e nessun filtro viene applicato -- stesso
pattern di degrado morbido usato in ner_pii.py per spaCy: nessuna
eccezione, solo un avviso nei log la prima volta.

Per abilitarlo:
    pip install langdetect
e imposta `output.use_language_filter: true` (con `allowed_languages`)
in config/sources.yaml.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("scrapellm.language_detector")

_LOAD_ATTEMPTED = False
_AVAILABLE = False
_SEEDED = False


def _ensure_loaded() -> bool:
    global _LOAD_ATTEMPTED, _AVAILABLE, _SEEDED

    if _LOAD_ATTEMPTED:
        return _AVAILABLE

    _LOAD_ATTEMPTED = True
    try:
        from langdetect import DetectorFactory
    except ImportError:
        logger.warning(
            "langdetect non installato: rilevamento lingua disattivato. "
            "Installa 'langdetect' (pip install langdetect) per abilitarlo."
        )
        _AVAILABLE = False
        return False

    if not _SEEDED:
        DetectorFactory.seed = 0  # risultati deterministici tra le run
        _SEEDED = True

    _AVAILABLE = True
    return True


def reset_cache() -> None:
    """Utile nei test per forzare un nuovo tentativo di import."""
    global _LOAD_ATTEMPTED, _AVAILABLE, _SEEDED
    _LOAD_ATTEMPTED = False
    _AVAILABLE = False
    _SEEDED = False


def is_available() -> bool:
    return _ensure_loaded()


def detect_language(text: str) -> Optional[str]:
    """Ritorna un codice lingua (es. "it", "en") o None se il rilevamento
    non e' disponibile, il testo e' vuoto, o troppo ambiguo per una stima
    affidabile. Non solleva mai eccezioni."""
    if not _ensure_loaded():
        return None
    if not text or not text.strip():
        return None

    from langdetect import detect
    from langdetect.lang_detect_exception import LangDetectException

    try:
        return detect(text)
    except LangDetectException:
        return None
