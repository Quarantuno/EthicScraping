"""Rilevamento PII avanzato via NER (opzionale), sopra al filtro regex.

Il filtro regex in pii_filter.py cattura solo pattern strutturali
(email, telefoni, IBAN...). Nomi propri di persona e luoghi specifici in
linguaggio naturale ("Mario Rossi abita a Via Roma 12, Cinisello
Balsamo") non hanno una struttura riconoscibile a regex: servono un
modello linguistico (Named Entity Recognition).

Questo modulo usa spaCy, che NON e' una dipendenza installata di
default (vedi requirements.txt) perche' e' pesante e richiede di
scaricare un modello a parte. Se spaCy o il modello non sono disponibili,
le funzioni qui sotto degradano silenziosamente (nessuna eccezione: il
testo torna invariato) cosi' che il resto della pipeline continui a
funzionare con la sola redazione regex.

Per abilitarlo:
    pip install spacy
    python -m spacy download it_core_news_sm
e imposta `output.use_ner: true` in config/sources.yaml.
"""
from __future__ import annotations

import logging
from typing import Optional, Set, Tuple

logger = logging.getLogger("scrapellm.ner_pii")

_NLP = None
_LOAD_ATTEMPTED = False
_LOADED_MODEL_NAME: Optional[str] = None

# PERSON/PER copre i nomi propri; GPE/LOC copre luoghi abbastanza
# specifici da poter contribuire a identificare una persona.
DEFAULT_LABELS = {"PERSON", "PER", "GPE", "LOC"}


def _load_model(model_name: str):
    """Carica (una sola volta, con cache) il modello spaCy richiesto.
    Se cambia model_name rispetto all'ultimo caricamento, ricarica.
    """
    global _NLP, _LOAD_ATTEMPTED, _LOADED_MODEL_NAME

    if _NLP is not None and _LOADED_MODEL_NAME == model_name:
        return _NLP
    if _LOAD_ATTEMPTED and _LOADED_MODEL_NAME == model_name and _NLP is None:
        return None

    _LOAD_ATTEMPTED = True
    _LOADED_MODEL_NAME = model_name

    try:
        import spacy
    except ImportError:
        logger.warning(
            "spaCy non installato: PII detection avanzata (nomi/luoghi) "
            "disattivata. Installa 'spacy' e un modello (es. "
            "'python -m spacy download %s') per abilitarla.",
            model_name,
        )
        _NLP = None
        return None

    try:
        _NLP = spacy.load(model_name)
    except OSError:
        logger.warning(
            "Modello spaCy '%s' non trovato: PII detection avanzata "
            "disattivata. Scaricalo con: python -m spacy download %s",
            model_name, model_name,
        )
        _NLP = None

    return _NLP


def reset_cache() -> None:
    """Utile nei test per forzare un nuovo tentativo di caricamento."""
    global _NLP, _LOAD_ATTEMPTED, _LOADED_MODEL_NAME
    _NLP = None
    _LOAD_ATTEMPTED = False
    _LOADED_MODEL_NAME = None


def is_available(model_name: str = "it_core_news_sm") -> bool:
    return _load_model(model_name) is not None


def redact_named_entities(text: str, model_name: str = "it_core_news_sm",
                            labels: Optional[Set[str]] = None) -> Tuple[str, dict]:
    """Redige nomi propri di persona e luoghi individuati via NER.

    Se il modello non e' disponibile ritorna (testo invariato, {}) --
    non solleva mai eccezioni per questo, cosi' la pipeline puo' sempre
    girare anche senza la dipendenza opzionale installata.
    """
    nlp = _load_model(model_name)
    if nlp is None:
        return text, {}

    active_labels = labels or DEFAULT_LABELS
    doc = nlp(text)

    spans = [(ent.start_char, ent.end_char, ent.label_) for ent in doc.ents
             if ent.label_ in active_labels]

    counts: dict = {}
    redacted = text
    # Sostituzione dalla fine verso l'inizio per non invalidare gli offset
    # delle entita' precedenti nella stringa.
    for start, end, label in sorted(spans, key=lambda s: s[0], reverse=True):
        redacted = redacted[:start] + f"[REDACTED_{label}]" + redacted[end:]
        counts[label] = counts.get(label, 0) + 1

    return redacted, counts
