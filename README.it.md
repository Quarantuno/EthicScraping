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

- **Scraper etico** (`scraper/`): rispetta sempre `robots.txt` —
  compreso cosa fare quando *non si riesce* a leggerlo, secondo la RFC
  9309: se manca (404) non ci sono regole, ma un errore del server o un
  server irraggiungibile significano "assumi che sia tutto vietato", e
  un 401/403/429 sul robots.txt stesso viene trattato come un rifiuto.
  Applica rate limiting per dominio (default 2s, o il `crawl-delay` del
  sito se maggiore), supporta una deny-list di domini da escludere
  sempre, e usa un User-Agent onesto e identificabile (niente finti
  browser). Errori di rete transitori, risposte 5xx e **429 Too Many
  Requests** vengono ritentati con backoff esponenziale, rispettando il
  `Retry-After` indicato dal server e rallentando per il resto della run
  tutte le richieste successive a quel dominio (mai ritentati: altri
  4xx o blocchi robots/deny-list); le risposte non HTML (PDF, immagini,
  JSON, ...) vengono scartate in base al `Content-Type` prima di tentare
  qualsiasi parsing. Una crawl puo' essere interrotta e ripresa in
  seguito senza riscaricare le pagine gia' visitate
  (`scraper/crawl_state.py`, `main.py run --reset-state` per ripartire
  da zero).
- **Crawler che segue i link** (`scraper/crawler.py`,
  `crawl.follow_links`): in ampiezza, non esce mai dal dominio del
  seed, rispetta `nofollow` (per l'intera pagina via meta robots /
  `X-Robots-Tag`, per il singolo link via `rel="nofollow"`), normalizza
  gli URL (niente `#frammenti` ne' parametri di tracciamento tipo
  `utm_*`/`fbclid`, cosi' la stessa pagina non viene scaricata due volte
  da link "decorati" diversamente), non richiede nemmeno i link che
  ovviamente non sono HTML (`.pdf`, immagini, archivi, ...), e puo'
  essere limitato per profondita' (`crawl.max_depth`) e con espressioni
  regolari di inclusione/esclusione sugli URL
  (`crawl.include_patterns` / `crawl.exclude_patterns`). I seed su
  domini diversi si possono scaricare in parallelo
  (`crawl.max_parallel_domains`) senza mai mandare a un sito più di una
  richiesta alla volta.
- **Download** in streaming: gli header si controllano prima del corpo
  (un PDF viene scartato senza scaricarlo), le pagine oltre
  `politeness.max_page_size_mb` (default 5) vengono abbandonate a metà
  download, e il testo viene decodificato secondo il charset dichiarato,
  poi `<meta charset>`, poi UTF-8 — non con il default ISO-8859-1 di
  requests, che rovina le lettere accentate delle pagine UTF-8 che non
  dichiarano il charset. Ogni record conserva il `<link rel="canonical">`
  della pagina, e la stessa pagina canonica viene scritta una sola volta
  per dataset. Quando si aggiungono record a un JSONL esistente, lo
  scraper ricostruisce anche gli indici per i duplicati esatti e simili.
- **Segnali di opt-out dall'AI** (`scraper/ai_optout.py`): oltre alla
  riserva TDM formale (vedi COMPLIANCE.it.md), le pagine con una
  direttiva esplicita `noai` in `<meta name="robots">` o nell'header
  `X-Robots-Tag` vengono scartate (`output.respect_noai`, attivo di
  default) — anche se hanno una licenza permissiva chiara, perche'
  `noai` e' il gestore che dice esplicitamente di non volere i suoi
  contenuti nell'addestramento di AI.
- **Rilevamento licenza** (`scraper/license_detector.py`): tenta di
  individuare la licenza di ogni pagina (link/meta `rel="license"`, un
  semplice `<a href="creativecommons.org/licenses/...">` anche senza
  l'attributo `rel="license"` — il caso piu' comune nella realta' —
  pattern Creative Commons nel testo visibile, domini noti come
  Wikipedia/Gutenberg). Se non trova nulla, la pagina viene etichettata
  come licenza "unknown" — mai assunta open di default.
- **Pipeline dati** (`pipeline/`): pulisce l'HTML in testo, filtra il
  testo di bassa qualita' (per lo piu' boilerplate/righe ripetute,
  codifica rovinata, "parole" assurdamente lunghe) con euristiche senza
  dipendenze esterne, opzionalmente filtra per lingua rilevata, **redige
  i dati personali** (email, telefoni, IBAN, IP, numeri tipo carta di
  credito via regex, con validazione checksum di Luhn e riconoscimento
  di parole-chiave bibliografiche cosi' che DOI/ISSN/numeri di
  brevetto/ProQuest/JSTOR/PMID/Handle/controllo di autorita' bibliotecario
  (LCCN/GND/VIAF/...) in testo enciclopedico e accademico non vengano
  scambiati per carte di credito o numeri di telefono -- controllato sia
  prima che dopo il match, perche' in una citazione di rivista le
  parole-chiave (Bibcode/doi/PMID/...) a volte vengono tutte dopo il
  numero, non prima -- e il riconoscimento dei telefoni non scavalca mai
  un a-capo, cosi' un numero non correlato sulla riga successiva (es. la
  cella dopo in una tabella) non puo' finire nello stesso falso positivo;
  opzionalmente anche nomi propri e luoghi via NER, vedi sotto), deduplica i contenuti (sia hash esatto sia quasi-duplicati
  via SimHash, cercati tramite un indice LSH a bande cosi' che il
  confronto non scandisca tutto il corpus a ogni documento), e scrive
  ogni record in JSONL con provenienza completa
  (URL, dominio, licenza, lingua, punteggio di qualita', timestamp di
  raccolta).
- **Revisione umana** (`pipeline/review.py`, comando `main.py review`):
  scorre il dataset prodotto e fa approvare/rifiutare ogni record a
  occhio umano, salvando lo stato cosi' da poter interrompere e
  riprendere. Produce `*_approved.jsonl` e `*_rejected.jsonl`.
- **CLI** (`main.py`): comandi `init` (configurazione guidata di
  `config/sources.yaml`), `discover-seeds` (URL candidati come seed
  letti dal `sitemap.xml` di un sito), `run` (scraping + pipeline),
  `merge` (unisce piu' file dataset con dedup tra i file), `review`
  (revisione umana) e `stats` (numero di record, distribuzione delle
  parole e della qualita' del testo, domini principali, concentrazione
  dei domini, mix di lingue, mix di licenze, PII redatte, reservation
  TDM residue di un dataset gia' prodotto), guidati da config
  YAML/opzioni. Installabile anche come comando `scrapellm`, vedi
  Installazione piu' sotto.

### Fase 2 — Fine-tuning (scaffold, non ancora eseguito end-to-end)

Cartella `training/`: preparazione dataset per il training e script di
fine-tuning LoRA su un modello open source. Vedi `training/README.md`
per i dettagli — **non lanciarlo su dati non revisionati**.

## PII: tre livelli

Prima di salvare ogni record, la pipeline applica gli stessi filtri al
titolo e al corpo della pagina. I conteggi includono entrambi i campi.
Gli URL originali e canonici sono conservati per la tracciabilità e
possono contenere identificativi.

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
3. **Presidio (opzionale)**: Microsoft Presidio come terzo livello,
   sopra ai due precedenti:
   ```bash
   pip install presidio-analyzer
   python -m spacy download it_core_news_sm   # o il modello della tua lingua
   ```
   poi `output.use_presidio: true` in `config/sources.yaml`. Stesso
   degrado morbido (avviso nei log, nessun blocco) se la dipendenza o il
   modello mancano. La lista di entity type di default e' volutamente
   mirata, non una semplice duplicazione dei livelli sopra: aggiunge
   **NRP** (nazionalita' / religione / orientamento politico —
   categoria "speciale" art. 9 GDPR che ne' il livello regex ne' il NER
   intercettano) piu' **CRYPTO** e **MEDICAL_LICENSE** (pattern rari, a
   basso rischio di falsi positivi), e mantiene **PERSON**/**LOCATION**
   (l'arricchimento contestuale di Presidio puo' individuare casi che il
   NER "nudo" di spaCy perde). Lascia volutamente disattivati di default
   i recognizer di Presidio per telefono/carta di credito/IBAN/email/IP:
   non conoscono il contesto bibliografico di citazione (vedi
   `pipeline/pii_filter.py`) e rischierebbero di reintrodurre proprio i
   falsi positivi DOI/ISSN/brevetti/bibliografici gia' risolti li'.
   Lascia disattivati anche date complete e URL — troppo comuni su testo
   enciclopedico/storico per essere un default sensato. Vedi
   `pipeline/presidio_pii.py` per la motivazione completa, e
   `output.presidio_entities` in `config/sources.example.yaml` per
   personalizzare la lista.

Nessuno dei tre livelli e' una garanzia assoluta, specialmente su fonti
ad alto rischio (forum, commenti, contenuti generati dagli utenti): per
quelle serve sempre una revisione umana (`main.py review`) prima
dell'uso.

### Valutazione — il modello e' davvero "consapevole"?

`eval/` contiene 64 prompt (32 in inglese, gli stessi 32 in italiano) in
7 categorie — provenienza dei dati, consenso e licenze, privacy,
resistenza al misuso, trasparenza, bias/limiti, e *calibrazione
dell'utilità* (richieste legittime con cui un buon modello dovrebbe
semplicemente aiutare, così un fine-tuning che ha imparato a rifiutare
tutto risulta un peggioramento, non un successo). `eval/run_eval.py` fa
girare un modello base o fine-tuned (con il suo chat template,
registrando esattamente come è stato eseguito); `eval/score.py` è la
parte umana: punteggio 0/1/2 a ogni risposta contro il suo rubric
(riprendibile), confronto **alla cieca** base vs fine-tuned, e report
per categoria e per lingua. I "red flag" via regex segnalano le risposte
sospette a chi valuta, ma non le punteggiano mai. Vedi
`eval/README.it.md`.

## Quadro normativo UE

Le scelte di design di questa pipeline (redazione PII, rilevamento
licenza, gestione opt-out TDM, tracciamento provenienza, revisione
umana) sono ancorate a specifiche disposizioni di GDPR, Direttiva
Copyright UE e AI Act rilevanti per lo scraping di dati per
l'addestramento di AI — non solo un generico "sii etico". Vedi
**[COMPLIANCE.it.md](COMPLIANCE.it.md)** per gli articoli veri e propri,
cosa e' automatizzato e cosa richiede ancora un umano (questo non e'
consulenza legale).

`compliance/generate_training_summary.py` compila anche la parte sui
dati scrappati dal web del riepilogo obbligatorio dei dati di training
richiesto dall'AI Act (art. 53(1)(d)) a partire dal tuo export del
dataset.

## Cosa NON fa ancora (prossimi passi possibili)

- Un training vero validato su hardware reale, e un passaggio di eval
  con `eval/` sul risultato (gli scaffold ci sono e sono testati in
  isolamento, ma non ancora eseguiti insieme end-to-end).

## Installazione

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Se la creazione del virtualenv da' problemi nel tuo ambiente, va bene
anche `pip install -r requirements.txt` diretto (senza venv), a costo di
installare le dipendenze a livello di sistema/utente.

In alternativa, installalo come pacchetto vero e proprio
(`pyproject.toml`, `pip install -e .`) e usa il comando `scrapellm` al
posto di `python main.py` in tutto quello che segue (`scrapellm run`,
`scrapellm init`, ...). Extra opzionali: `pip install -e ".[ner]"`
(spaCy), `.[lang]` (langdetect), `.[presidio]` (Microsoft Presidio),
`.[full]` (tutti e tre), `.[dev]` (strumenti per i test).

### Accelerazione Rust opzionale per SimHash

Il calcolo dei voti di SimHash a 64 bit può usare una libreria nativa
Rust. Python gestisce tokenizzazione, hashing, crawl e scrittura del dataset.

```bash
rustc --edition 2021 --crate-type cdylib -O native/simhash.rs -o libethic_simhash.dylib
SCRAPELLM_SIMHASH_LIB="$PWD/libethic_simhash.dylib" python3 main.py run --config config/sources.yaml
```

Usa l’estensione adatta al sistema (`.dylib` su macOS, `.so` su Linux).
Senza `SCRAPELLM_SIMHASH_LIB` viene usata l’implementazione Python.
I dataset esistenti e le loro impronte restano compatibili.

## Uso

### 0. Avvio rapido: configurazione guidata

```bash
python main.py init
```

Copia `config/sources.example.yaml` in `config/sources.yaml`, facendo
tre domande rapide (nome progetto, User-Agent onesto, primo seed URL) e
compilandole senza toccare i commenti esplicativi del file di esempio.
`--non-interactive` copia l'esempio cosi' com'e' (per script/CI),
`--force` sovrascrive un `config/sources.yaml` gia' esistente. Alla
fine stampa un riepilogo di validazione, oppure `--help` per tutte le
opzioni. Puoi anche fare il passo 1 a mano invece di usare questo.

### 1. Configura e lancia lo scraping

```bash
cp config/sources.example.yaml config/sources.yaml
```

Modifica `config/sources.yaml`:
- **`project.user_agent`**: metti un contatto reale (email o URL del
  progetto). Il tool si rifiuta di partire con lo user-agent di esempio.
- **`seeds`**: le URL di partenza. Non vuoi sceglierle a mano? Vedi
  "Scopri seed da una sitemap" piu' sotto.
- **`deny_domains`**: domini da escludere sempre.
- **`output.min_license_confidence`**: `null` per raccogliere tutto (con
  etichetta di licenza), `"high"` per tenere solo contenuti con licenza
  chiaramente identificata.
- **`output.near_duplicate_threshold`**: soglia per la deduplica fuzzy
  (default 8; vedi i commenti nel file).
- **`output.use_ner`** / **`ner_model`**: attiva la redazione PII
  avanzata (richiede spaCy, vedi sopra).
- **`output.use_presidio`** / **`presidio_language`** / **`presidio_model`**
  / **`presidio_entities`**: attiva il terzo livello PII (richiede
  Microsoft Presidio, vedi "PII: tre livelli" sopra).
- **`output.respect_noai`**: scarta le pagine con opt-out esplicito
  `noai` (default `true`, vedi "Segnali di opt-out dall'AI" sopra).
- **`crawl.follow_links`** / **`max_depth`** / **`include_patterns`** /
  **`exclude_patterns`**: segue i link interni allo stesso dominio, e
  fin dove/quali.
- **`crawl.max_parallel_domains`**: quanti domini diversi scaricare
  contemporaneamente (default 1, in sequenza).
- **`politeness.max_page_size_mb`**: scarta le pagine più grandi di così
  (default 5).
- **`politeness.max_retry_after_seconds`**: il `Retry-After` piu' lungo
  che siamo disposti ad aspettare davvero (default 120s); oltre,
  rinunciamo a quell'URL per questa run invece di bloccare tutto.

```bash
python main.py run --config config/sources.yaml --verbose
```

Aggiungi `--report-json` per stampare un riepilogo JSON su stdout mantenendo
i log su stderr. Il rapporto conta gli esiti delle richieste (inclusi i blocchi
di robots.txt e gli errori di download) e della pipeline (inclusi gli scarti
per licenza, opt-out e duplicati). Il conteggio degli URL visitati è cumulativo
quando lo stato di crawl è attivo.

```bash
python main.py run --config config/sources.yaml --report-json > crawl-report.json
```

Run interrotto a meta'? Basta rilanciare lo stesso comando: le pagine
gia' scaricate (con successo, bloccate da robots, in deny-list, o con
content-type sbagliato) vengono saltate automaticamente, tracciate in un
file `*.crawl_state.json` accanto al dataset. Passa `--reset-state` per
ignorarlo e ripartire da zero.

```bash
python main.py stats --dataset dataset/output.jsonl
```

Con `--html` puoi generare un report per il browser con grafici e ricerca
tra i domini mostrati. Contiene conteggi aggregati e si apre in locale
senza bisogno di un server.

```bash
python main.py stats --dataset dataset/output.jsonl --html > statistiche.html
```

Riepilogo rapido di un dataset gia' prodotto: numero di record,
distribuzione delle parole, domini principali, mix di licenze, PII
redatte, e quanti record portano ancora una reservation TDM o un opt-out `noai`.

Il dataset viene scritto come JSONL in `dataset/output.jsonl` (path
configurabile). Ogni riga e' un record con questo schema:

```json
{
  "id": "uuid",
  "url": "https://...",
  "canonical_url": "https://... (o null)",
  "domain": "esempio.com",
  "title": "...",
  "text": "testo pulito, con PII redatta",
  "word_count": 123,
  "content_hash": "sha256 del testo normalizzato",
  "license": "CC BY-SA 4.0",
  "license_source": "rel_license_link",
  "license_confidence": "high",
  "tdm_reservation": false,
  "ai_optout": false,
  "language": "it",
  "quality_score": 0.87,
  "pii_redactions": {"EMAIL": 2},
  "fetched_at": 1234567890.0,
  "collected_at": 1234567890.0
}
```

`language` e' `null` a meno che non sia installato il pacchetto
opzionale `langdetect` (vedi `requirements.txt`) -- senza, non viene
applicato nemmeno alcun filtro lingua, solo un `null` informativo.
`quality_score` (0-1, piu' alto e' meglio) viene sempre calcolato con
euristiche senza dipendenze esterne (`pipeline/quality_filter.py`), a
prescindere dal fatto che `output.use_quality_filter` scarti o meno le
pagine con punteggio basso.

### 2. Revisiona il dataset

```bash
python main.py review --dataset dataset/output.jsonl
```

Ti mostra un record alla volta (URL, licenza, redazioni PII, anteprima
del testo) e chiede `[a]pprova / [r]ifiuta / [s]alta / [q]uit`. Le
decisioni si salvano subito su disco: puoi interrompere e riprendere
quando vuoi, i record gia' decisi non vengono riproposti. Risultato:
`dataset/output_approved.jsonl` e `dataset/output_rejected.jsonl`.

### 3. Scopri seed da una sitemap

```bash
python main.py discover-seeds --sitemap https://esempio.it/sitemap.xml --config config/sources.yaml
```

Legge il `sitemap.xml` di un sito (seguendo anche un sitemap index --
una sitemap che elenca altre sitemap -- se e' quello che trova) e
stampa gli URL di pagina trovati, come candidati per `seeds:`. Rispetta
la stessa etica di ogni altro fetch di questo progetto (`robots.txt`,
rate limiting, User-Agent onesto, letto da `--config` o passato
direttamente con `--user-agent`). Non tocca `config/sources.yaml` da
solo -- usa `--contains` per filtrare per sottostringa dell'URL,
`--limit` per un tetto massimo, `--output seeds.yaml` per scrivere un
blocco YAML `seeds:` pronto da incollare invece di stamparlo. Decidere
cosa mettere davvero in `seeds:` resta volutamente a te, vedi la
checklist qui sotto.

### 4. Unisci piu' file di dataset

```bash
python main.py merge --inputs dataset/run1.jsonl --inputs dataset/run2.jsonl --output dataset/merged.jsonl
```

Combina piu' file JSONL prodotti da run separate (seed set diversi,
giorni diversi, ...) in uno solo, deduplicando tra TUTTI insieme con lo
stesso meccanismo hash esatto + near-duplicate SimHash usato
internamente da `run`, cosi' che un articolo finito in due file
separati sopravviva una volta sola. `--exact-only` disattiva il
controllo fuzzy, `--no-dedup` disattiva la deduplica del tutto
(concatenazione pura).

### 5. (Opzionale) Fase 2 — fine-tuning

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
6. **Se sei nell'UE o ti rivolgi a utenti UE:** vedi
   [COMPLIANCE.it.md](COMPLIANCE.it.md) per come GDPR, l'eccezione TDM
   della Direttiva Copyright e l'AI Act si applicano a questo passaggio
   specifico.

## Test

```bash
python3 -m unittest discover -s tests -v
```

336 test coprono la logica pura di scraper (incluso retry/backoff,
gestione di 429/`Retry-After`, semantica degli errori su robots.txt (RFC
9309), rilevamento dell'opt-out `noai`, normalizzazione URL/`nofollow`/
profondita'/pattern del crawler, filtro
Content-Type, parsing di sitemap/sitemap-index, e il rilevamento licenza
in tutti i suoi percorsi di fallback, con `requests.get` mockato),
pipeline (incluse le euristiche di qualita' del testo, il degrado del
rilevamento lingua, il degrado morbido del livello PII Presidio quando
la dipendenza manca, e una verifica di correttezza dell'indice LSH a
bande per la deduplica contro un confronto di riferimento a forza
bruta), i comandi CLI `init`/`discover-seeds`/`merge`/`run` end-to-end
via `click.testing.CliRunner`, ripresa della crawl, revisione,
preparazione dati per il training, e gli strumenti di valutazione
(integrità del set di prompt nelle due lingue, rendering del chat
template, il ciclo di generazione con moduli `torch`/`transformers`
finti, punteggi, confronto alla cieca e report) —
nessuno richiede rete o dipendenze pesanti (torch/spaCy/langdetect/
presidio-analyzer non servono per farli passare, il codice degrada
correttamente quando non sono installati).

Gira automaticamente su ogni push/PR a `main` via GitHub Actions
(`.github/workflows/tests.yml`) su Python 3.9 (su Ubuntu 24.04) e
3.11/3.12/3.13.

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
│   ├── tdm_rights.py    # rilevamento opt-out art. 4(3) Direttiva 2019/790 (TDMRep)
│   ├── ai_optout.py      # opt-out "noai" in meta robots / X-Robots-Tag
│   ├── fetcher.py        # retry con backoff + filtro Content-Type
│   ├── crawl_state.py    # crawling ripristinabile (stato URL visitati persistito)
│   ├── urls.py           # normalizzazione URL + rel=canonical
│   ├── sitemap.py         # parsing sitemap/sitemap-index per `discover-seeds`
│   └── crawler.py
├── pipeline/           # pulizia, PII, dedup, revisione, scrittura dataset
│   ├── text_extractor.py
│   ├── pii_filter.py
│   ├── ner_pii.py       # PII avanzata via NER (opzionale, spaCy)
│   ├── presidio_pii.py   # terzo livello PII (opzionale, Microsoft Presidio)
│   ├── dedup.py          # hash esatto + SimHash/indice LSH per i quasi-duplicati
│   ├── review.py         # revisione umana (stato persistito)
│   ├── quality_filter.py # euristiche di qualita' del testo, senza dipendenze
│   ├── language_detector.py # rilevamento lingua opzionale (langdetect)
│   ├── dataset_stats.py  # statistiche riepilogo per `main.py stats`
│   └── dataset_writer.py
├── training/            # fase 2: fine-tuning LoRA (scaffold)
│   ├── prepare_dataset.py
│   ├── train_lora.py
│   ├── config.example.yaml
│   ├── requirements-training.txt
│   └── README.md
├── eval/                # valutazione della "consapevolezza" (EN + IT)
│   ├── prompts.jsonl     # 64 prompt, rubric, segnali (red flag)
│   ├── run_eval.py       # genera le risposte (base / fine-tuned)
│   ├── score.py          # punteggi umani, confronto alla cieca A/B, report
│   └── README.md         # (+ README.it.md)
├── compliance/           # generatore bozza riepilogo dati per AI Act
│   └── generate_training_summary.py
├── .github/workflows/     # CI: esegue i test su ogni push/PR (tests.yml)
├── COMPLIANCE.md          # mappatura GDPR / AI Act / Direttiva Copyright (+ .it.md)
├── config/
│   └── sources.example.yaml
├── dataset/             # output JSONL (ignorato da git)
├── tests/               # 336 unit test, nessuna dipendenza pesante
├── main.py              # CLI: init, discover-seeds, run, merge, review, stats
├── pyproject.toml         # packaging: `pip install -e .` -> comando `scrapellm`
├── requirements.txt
└── LICENSE              # MIT
```

## Licenza del progetto

MIT — vedi `LICENSE`.
