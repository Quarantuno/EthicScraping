# ScrapeLLM

*[Read this in English](README.md)*

Pipeline open source e gratuita per raccogliere dati dal web in modo
**etico e tracciabile**, rivederli, e trasformarli in dataset pronti per
addestrare modelli AI a un uso consapevole dei dati.

L'idea di fondo: un modello addestrato su dati raccolti senza cura del
consenso, della licenza e della privacy non puo' essere "consapevole" di
nulla. Quindi la consapevolezza si costruisce a monte, nella pipeline di
raccolta e revisione, non solo nel prompt del modello finale.

## Cosa fa

### Fase 1 — Scraping etico + costruzione dataset

- **Scraper etico** (`scraper/`): rispetta sempre `robots.txt`, applica
  rate limiting per dominio (default 2s, o il `crawl-delay` del sito se
  maggiore), supporta una deny-list di domini da escludere sempre, e usa
  un User-Agent onesto e identificabile (niente finti browser).
- **Rilevamento licenza** (`scraper/license_detector.py`): tenta di
  individuare la licenza di ogni pagina (link/meta `rel="license"`,
  pattern Creative Commons, domini noti come Wikipedia/Gutenberg). Se non
  trova nulla, la pagina viene etichettata come licenza "unknown" — mai
  assunta open di default.
- **Pipeline dati** (`pipeline/`): pulisce l'HTML in testo, **redige i
  dati personali** (email, telefoni, IBAN, IP, numeri tipo carta di
  credito via regex; opzionalmente anche nomi propri e luoghi via NER,
  vedi sotto), deduplica i contenuti (sia hash esatto sia quasi-duplicati
  via SimHash), e scrive ogni record in JSONL con provenienza completa
  (URL, dominio, licenza, timestamp di raccolta).
- **Revisione umana** (`pipeline/review.py`, comando `main.py review`):
  scorre il dataset prodotto e fa approvare/rifiutare ogni record a
  occhio umano, salvando lo stato cosi' da poter interrompere e
  riprendere. Produce `*_approved.jsonl` e `*_rejected.jsonl`.
- **CLI** (`main.py`): comandi `run` (scraping + pipeline) e `review`
  (revisione umana), entrambi guidati da config YAML/opzioni.

### Fase 2 — Fine-tuning (scaffold, non ancora eseguito end-to-end)

Cartella `training/`: preparazione dataset per il training e script di
fine-tuning LoRA su un modello open source. Vedi `training/README.md`
per i dettagli — **non lanciarlo su dati non revisionati**.

## PII: due livelli

1. **Regex (sempre attivo)**: email, telefoni, IBAN, IP, numeri tipo
   carta di credito — pattern strutturali, veloce, nessuna dipendenza
   pesante.
2. **NER (opzionale)**: nomi propri e luoghi in linguaggio naturale, via
   spaCy. Disattivato di default perche' e' una dipendenza pesante da
   installare a parte:
   ```bash
   pip install spacy
   python -m spacy download it_core_news_sm
   ```
   poi `output.use_ner: true` in `config/sources.yaml`. Se spaCy o il
   modello non sono installati, il sistema degrada da solo alla sola
   redazione regex (con un avviso nei log), senza bloccarsi.

Nessuno dei due e' una garanzia assoluta, specialmente su fonti ad alto
rischio (forum, commenti, contenuti generati dagli utenti): per quelle
serve sempre una revisione umana (`main.py review`) prima dell'uso.

### Valutazione — il modello e' davvero "consapevole"?

`eval/` contiene 18 prompt su 6 categorie (provenienza dei dati,
consenso, privacy, resistenza al misuso, trasparenza, bias/limiti) da
lanciare contro un modello addestrato e da leggere a mano contro un
rubric -- nessun punteggio automatico, e' pensato per un giudizio umano.
Vedi `eval/README.md`.

## Cosa NON fa ancora (prossimi passi possibili)

- Un training vero validato su hardware reale, e un passaggio di eval
  con `eval/` sul risultato (gli scaffold ci sono e sono testati in
  isolamento, ma non ancora eseguiti insieme end-to-end).
- Indice LSH per la deduplica fuzzy su larga scala (oggi confronto O(n)
  per documento, adatto a dataset di migliaia di pagine, non milioni).

## Installazione

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Se la creazione del virtualenv da' problemi nel tuo ambiente, va bene
anche `pip install -r requirements.txt` diretto (senza venv), a costo di
installare le dipendenze a livello di sistema/utente.

## Uso

### 1. Configura e lancia lo scraping

```bash
cp config/sources.example.yaml config/sources.yaml
```

Modifica `config/sources.yaml`:
- **`project.user_agent`**: metti un contatto reale (email o URL del
  progetto). Il tool si rifiuta di partire con lo user-agent di esempio.
- **`seeds`**: le URL di partenza.
- **`deny_domains`**: domini da escludere sempre.
- **`output.min_license_confidence`**: `null` per raccogliere tutto (con
  etichetta di licenza), `"high"` per tenere solo contenuti con licenza
  chiaramente identificata.
- **`output.near_duplicate_threshold`**: soglia per la deduplica fuzzy
  (default 8; vedi i commenti nel file).
- **`output.use_ner`** / **`ner_model`**: attiva la redazione PII
  avanzata (richiede spaCy, vedi sopra).

```bash
python main.py run --config config/sources.yaml --verbose
```

Il dataset viene scritto come JSONL in `dataset/output.jsonl` (path
configurabile). Ogni riga e' un record con questo schema:

```json
{
  "id": "uuid",
  "url": "https://...",
  "domain": "esempio.com",
  "title": "...",
  "text": "testo pulito, con PII redatta",
  "word_count": 123,
  "content_hash": "sha256 del testo normalizzato",
  "license": "CC BY-SA 4.0",
  "license_source": "rel_license_link",
  "license_confidence": "high",
  "pii_redactions": {"EMAIL": 2},
  "fetched_at": 1234567890.0,
  "collected_at": 1234567890.0
}
```

### 2. Revisiona il dataset

```bash
python main.py review --dataset dataset/output.jsonl
```

Ti mostra un record alla volta (URL, licenza, redazioni PII, anteprima
del testo) e chiede `[a]pprova / [r]ifiuta / [s]alta / [q]uit`. Le
decisioni si salvano subito su disco: puoi interrompere e riprendere
quando vuoi, i record gia' decisi non vengono riproposti. Risultato:
`dataset/output_approved.jsonl` e `dataset/output_rejected.jsonl`.

### 3. (Opzionale) Fase 2 — fine-tuning

Vedi `training/README.md`. In breve:

```bash
python training/prepare_dataset.py --input dataset/output_approved.jsonl --output-dir training/data
pip install -r training/requirements-training.txt
cp training/config.example.yaml training/config.yaml   # personalizza base_model ecc.
python training/train_lora.py --config training/config.yaml --check   # valida senza scaricare nulla
python training/train_lora.py --config training/config.yaml           # training vero
```

## Checklist etica/legale prima di aggiungere una fonte

Prima di aggiungere un seed a `config/sources.yaml`, chiediti:

1. **Il sito lo permette?** Controlla `robots.txt` e i Termini di
   Servizio del sito, non solo se `robots.txt` non blocca tecnicamente
   la pagina.
2. **Che licenza hanno i contenuti?** Se non e' chiara, considera di
   impostare `min_license_confidence: "high"` per escludere
   automaticamente quella fonte dal dataset finale, anche se viene
   comunque scaricata per ispezione.
3. **Ci sono dati personali?** I filtri automatici (regex + NER
   opzionale) sono una prima difesa, non una garanzia. Su fonti ad alto
   rischio (forum, social, commenti) usa sempre `main.py review` prima
   di considerare i dati pronti per il training.
4. **Il sito ha chiesto di non essere scrappato?** Se un gestore ti
   contatta chiedendo l'esclusione, aggiungilo a `deny_domains` subito.
5. **Serve davvero?** Preferisci fonti con licenza aperta esplicita
   (Wikipedia, Project Gutenberg, dataset pubblici, repository open
   source) quando l'obiettivo e' costruire un dataset riusabile e
   redistribuibile.

## Test

```bash
python3 -m unittest discover -s tests -v
```

35 test coprono la logica pura di scraper, pipeline, revisione e
preparazione dati per il training — nessuno richiede rete o dipendenze
pesanti (torch/spaCy non servono per farli passare, il codice degrada
correttamente quando non sono installati).

Un run reale di scraping (`python main.py run --config ...`) richiede
invece una connessione di rete in uscita verso i siti target: se lo
lanci da un ambiente con egress ristretto, i fetch falliranno con errori
di connessione anche se la logica e' corretta.

## Struttura del progetto

```
ScrapeLLM/
├── scraper/            # fetch etico: robots.txt, rate limit, licenza
│   ├── robots.py
│   ├── rate_limiter.py
│   ├── license_detector.py
│   ├── fetcher.py
│   └── crawler.py
├── pipeline/           # pulizia, PII, dedup, revisione, scrittura dataset
│   ├── text_extractor.py
│   ├── pii_filter.py
│   ├── ner_pii.py       # PII avanzata via NER (opzionale, spaCy)
│   ├── dedup.py          # hash esatto + SimHash per i quasi-duplicati
│   ├── review.py         # revisione umana (stato persistito)
│   └── dataset_writer.py
├── training/            # fase 2: fine-tuning LoRA (scaffold)
│   ├── prepare_dataset.py
│   ├── train_lora.py
│   ├── config.example.yaml
│   ├── requirements-training.txt
│   └── README.md
├── eval/                # prompt di valutazione "consapevolezza" + runner
│   ├── prompts.jsonl
│   ├── run_eval.py
│   └── README.md
├── config/
│   └── sources.example.yaml
├── dataset/             # output JSONL (ignorato da git)
├── tests/               # 35 unit test, nessuna dipendenza pesante
├── main.py              # CLI: run, review
├── requirements.txt
└── LICENSE              # MIT
```

## Licenza del progetto

MIT — vedi `LICENSE`.
