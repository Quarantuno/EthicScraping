# ScrapeLLM

Pipeline open source e gratuita per raccogliere dati dal web in modo
**etico e tracciabile**, e trasformarli in dataset pronti per addestrare
modelli AI a un uso consapevole dei dati.

L'idea di fondo: un modello addestrato su dati raccolti senza cura del
consenso, della licenza e della privacy non puo' essere "consapevole"
di nulla. Quindi la consapevolezza si costruisce a monte, nella pipeline
di raccolta, non solo nel prompt del modello finale.

## Cosa fa (stato attuale: fase 1 - scraping + dataset)

- **Scraper etico** (`scraper/`): rispetta sempre `robots.txt`, applica
  rate limiting per dominio (default 2s, o il `crawl-delay` del sito se
  maggiore), supporta una deny-list di domini da escludere sempre, e usa
  un User-Agent onesto e identificabile (niente finti browser).
- **Rilevamento licenza** (`scraper/license_detector.py`): tenta di
  individuare la licenza di ogni pagina (link `rel="license"`, meta tag,
  domini noti come Wikipedia/Gutenberg). Se non trova nulla, la pagina
  viene etichettata come licenza "unknown" — mai assunta open di default.
- **Pipeline dati** (`pipeline/`): pulisce l'HTML in testo, **redige i
  dati personali** (email, telefoni, IBAN, IP, numeri tipo carta di
  credito) con un filtro regex, deduplica i contenuti, e scrive ogni
  record in JSONL con provenienza completa (URL, dominio, licenza,
  timestamp di raccolta).
- **CLI** (`main.py`): lancia l'intera pipeline da un file di
  configurazione YAML.

## Cosa NON fa ancora (prossimi passi possibili)

- Fine-tuning/addestramento del modello sui dati raccolti (fase 2).
- Rilevamento PII avanzato basato su NLP (nomi propri, indirizzi in
  linguaggio naturale) — oggi solo pattern regex strutturali. Per un
  dataset destinato a produzione, valutare l'integrazione di
  [Presidio](https://github.com/microsoft/presidio) + spaCy (dipendenze
  gia' predisposte, commentate, in `requirements.txt`).
- Deduplica "fuzzy" (simhash/minhash) per contenuti quasi identici.
- Interfaccia per revisione umana dei dati prima dell'inclusione nel
  dataset finale.

## Installazione

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Uso

1. Copia il file di configurazione di esempio e personalizzalo:

   ```bash
   cp config/sources.example.yaml config/sources.yaml
   ```

2. Modifica `config/sources.yaml`:
   - **`project.user_agent`**: metti un contatto reale (email o URL del
     progetto). Il tool si rifiuta di partire con lo user-agent di
     esempio.
   - **`seeds`**: le URL di partenza.
   - **`deny_domains`**: domini da escludere sempre.
   - **`output.min_license_confidence`**: lascia `null` per raccogliere
     tutto (con etichetta di licenza), oppure `"high"` per tenere solo
     contenuti con licenza chiaramente identificata.

3. Lancia lo scraping:

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
  "license": "CC BY-SA 4.0" ,
  "license_source": "rel_license_link",
  "license_confidence": "high",
  "pii_redactions": {"EMAIL": 2},
  "fetched_at": 1234567890.0,
  "collected_at": 1234567890.0
}
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
3. **Ci sono dati personali?** Il filtro PII e' una prima difesa, non
   una garanzia. Su fonti ad alto rischio (forum, social, commenti)
   serve revisione umana o un filtro NLP piu' robusto prima dell'uso.
4. **Il sito ha chiesto di non essere scrappato?** Se un gestore ti
   contatta chiedendo l'esclusione, aggiungilo a `deny_domains` subito.
5. **Serve davvero?** Preferisci fonti con licenza aperta esplicita
   (Wikipedia, Project Gutenberg, dataset pubblici, repository open
   source) quando l'obiettivo e' costruire un dataset riusabile e
   redistribuibile.

## Test

I test unitari coprono la logica pura (estrazione testo, redazione PII,
dedup, rilevamento licenza, scrittura dataset) senza bisogno di rete:

```bash
python3 -m unittest discover -s tests -v
```

Un test end-to-end con fetch reali (`python main.py run --config ...`)
richiede una connessione di rete in uscita verso i siti target: se lo
lanci da un ambiente con egress ristretto o senza rete, i fetch
falliranno con errori di connessione anche se la logica e' corretta.

## Struttura del progetto

```
ScrapeLLM/
├── scraper/            # fetch etico: robots.txt, rate limit, licenza
│   ├── robots.py
│   ├── rate_limiter.py
│   ├── license_detector.py
│   ├── fetcher.py
│   └── crawler.py
├── pipeline/           # pulizia, PII, dedup, scrittura dataset
│   ├── text_extractor.py
│   ├── pii_filter.py
│   ├── dedup.py
│   └── dataset_writer.py
├── config/
│   └── sources.example.yaml
├── dataset/            # output JSONL (ignorato da git)
├── tests/
├── main.py             # CLI
└── requirements.txt
```

## Licenza del progetto

Da definire (consigliata: MIT o Apache 2.0, coerente con lo spirito
open source dichiarato). Aggiungi un file `LICENSE` quando decidi.
