"""Rilevamento PII via Microsoft Presidio (opzionale): terzo livello
sopra al filtro regex (sempre attivo, pii_filter.py) e al NER via spaCy
(pipeline/ner_pii.py).

Perche' un terzo livello, e perche' proprio questi entity type di
default
-------------------------------------------------------------------
Presidio e' un motore generico con i propri recognizer per telefono,
carta di credito, IBAN, email e IP -- ma quei recognizer non conoscono
il contesto bibliografico (DOI/ISSN/numeri di brevetto/Bibcode/PMID/
identificatori di autorita' bibliotecaria) che il filtro regex di
questo progetto ha imparato a riconoscere attraverso diversi round di
test su dati reali (vedi pii_filter.py e la cronologia dei commit).
Abilitare quei tipi via Presidio rischierebbe di reintrodurre esattamente
i falsi positivi gia' risolti li'. Per questo PHONE_NUMBER/CREDIT_CARD/
IBAN_CODE/EMAIL_ADDRESS/IP_ADDRESS sono esclusi dalla lista di default
(restano gestiti, meglio, dal filtro regex).

DATE_TIME e URL sono esclusi anch'essi di default: su testo
enciclopedico/storico le date sono normalissime (un articolo su un
evento storico ne e' pieno) e gli URL di citazione pure -- redigerli
di default degraderebbe pesantemente l'utilita' del dataset per un
guadagno di privacy minimo.

Il valore aggiunto scelto come default e' quindi deliberatamente
mirato a cio' che NON e' gia' coperto altrove:
- NRP (nazionalita' / religione / orientamento politico): categoria
  "speciale" ai sensi dell'art. 9 GDPR, che ne' il filtro regex ne'
  ner_pii.py (solo PERSON/LOC) intercettano oggi.
- CRYPTO (indirizzi di wallet crypto) e MEDICAL_LICENSE: pattern rari
  e strutturalmente distintivi, basso rischio di falsi positivi su
  testo enciclopedico/divulgativo.
- PERSON / LOCATION: in parte ridondanti con ner_pii.py, ma inclusi
  comunque perche' l'arricchimento contestuale di Presidio puo'
  individuare casi che il NER "nudo" di spaCy perde.

Un utente che capisce i rischi puo' comunque passare una lista
`entities` personalizzata (es. per riabilitare DATE_TIME su un corpus
dove le date sono effettivamente sensibili).

Stessa filosofia di degradazione automatica del resto del progetto: se
presidio-analyzer (o un modello spaCy per la lingua scelta) non sono
installati, le funzioni qui sotto tornano il testo invariato con un
avviso nei log, senza mai sollevare eccezioni.

Per abilitarlo:
    pip install presidio-analyzer
    python -m spacy download it_core_news_sm   # o il modello della lingua scelta
e imposta `output.use_presidio: true` in config/sources.yaml.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence, Tuple

logger = logging.getLogger("scrapellm.presidio_pii")

_ENGINE = None
_LOAD_ATTEMPTED = False
_LOADED_KEY: Optional[Tuple[str, str]] = None

# Vedi la spiegazione in cima al modulo per il perche' di questa scelta.
DEFAULT_ENTITIES = ("PERSON", "LOCATION", "NRP", "CRYPTO", "MEDICAL_LICENSE")


def _load_engine(language: str, spacy_model: str):
    """Carica (una sola volta per coppia lingua/modello, con cache) un
    AnalyzerEngine di Presidio configurato sul modello spaCy dato. Se
    cambia la coppia rispetto all'ultimo caricamento, ricarica."""
    global _ENGINE, _LOAD_ATTEMPTED, _LOADED_KEY

    key = (language, spacy_model)
    if _ENGINE is not None and _LOADED_KEY == key:
        return _ENGINE
    if _LOAD_ATTEMPTED and _LOADED_KEY == key and _ENGINE is None:
        return None

    _LOAD_ATTEMPTED = True
    _LOADED_KEY = key

    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider
    except ImportError:
        logger.warning(
            "presidio-analyzer non installato: il terzo livello di PII "
            "detection e' disattivato. Installa 'presidio-analyzer' e un "
            "modello spaCy (es. 'python -m spacy download %s') per abilitarlo.",
            spacy_model,
        )
        _ENGINE = None
        return None

    try:
        provider = NlpEngineProvider(nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": language, "model_name": spacy_model}],
        })
        nlp_engine = provider.create_engine()
        _ENGINE = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=[language])
    except Exception as exc:
        # Copre sia il caso del modello spaCy mancante sia altri errori
        # di inizializzazione di Presidio: l'obiettivo e' non bloccare
        # mai la pipeline per una dipendenza opzionale.
        logger.warning(
            "Impossibile inizializzare Presidio (lingua='%s', modello='%s'): %s. "
            "Terzo livello di PII detection disattivato. Scarica il modello con: "
            "python -m spacy download %s",
            language, spacy_model, exc, spacy_model,
        )
        _ENGINE = None

    return _ENGINE


def reset_cache() -> None:
    """Utile nei test per forzare un nuovo tentativo di caricamento."""
    global _ENGINE, _LOAD_ATTEMPTED, _LOADED_KEY
    _ENGINE = None
    _LOAD_ATTEMPTED = False
    _LOADED_KEY = None


def is_available(language: str = "it", spacy_model: str = "it_core_news_sm") -> bool:
    return _load_engine(language, spacy_model) is not None


def redact_with_presidio(text: str, language: str = "it",
                          spacy_model: str = "it_core_news_sm",
                          entities: Optional[Sequence[str]] = None,
                          score_threshold: float = 0.5) -> Tuple[str, dict]:
    """Redige le entita' individuate da Presidio.

    Se Presidio o il modello non sono disponibili ritorna (testo
    invariato, {}) -- non solleva mai eccezioni per questo, cosi' la
    pipeline puo' sempre girare anche senza la dipendenza opzionale
    installata. `entities` sovrascrive DEFAULT_ENTITIES quando passato.
    """
    if not text:
        return text, {}

    engine = _load_engine(language, spacy_model)
    if engine is None:
        return text, {}

    active_entities = list(entities) if entities is not None else list(DEFAULT_ENTITIES)

    try:
        results = engine.analyze(text=text, language=language,
                                  entities=active_entities,
                                  score_threshold=score_threshold)
    except Exception as exc:
        logger.warning("Errore durante l'analisi Presidio: %s. Testo non modificato.", exc)
        return text, {}

    counts: dict = {}
    redacted = text
    # Sostituzione dalla fine verso l'inizio per non invalidare gli
    # offset delle entita' precedenti nella stringa (stesso approccio di
    # ner_pii.py).
    for result in sorted(results, key=lambda r: r.start, reverse=True):
        label = result.entity_type
        redacted = redacted[:result.start] + f"[REDACTED_{label}]" + redacted[result.end:]
        counts[label] = counts.get(label, 0) + 1

    return redacted, counts
