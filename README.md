# ScrapeLLM

*[Leggi questo in italiano](README.it.md)*

Open source, free pipeline for collecting web data in an **ethical,
traceable** way, reviewing it by hand, and turning it into datasets
ready for training AI models on responsible data use.

The core idea: a model trained on data collected without care for
consent, licensing, and privacy can't be "responsible" about anything.
So that responsibility has to be built upstream, into the collection and
review pipeline — not just into the final model's prompt.

## What it does

### Phase 1 — Ethical scraping + dataset building

- **Ethical scraper** (`scraper/`): always honors `robots.txt`, applies
  per-domain rate limiting (2s default, or the site's own `crawl-delay`
  if higher), supports a deny-list of domains to always skip, and uses
  an honest, identifiable User-Agent (no fake browsers). Transient
  network errors and 5xx responses are retried with exponential
  backoff (never 4xx or a robots/deny-list block); non-HTML responses
  (PDFs, images, JSON, ...) are discarded by `Content-Type` before any
  parsing is attempted. A crawl can be interrupted and resumed later
  without re-fetching pages it already visited (`scraper/crawl_state.py`,
  `main.py run --reset-state` to start over).
- **License detection** (`scraper/license_detector.py`): best-effort
  detection of each page's license (`rel="license"` link/meta tag,
  Creative Commons patterns, known domains like Wikipedia/Gutenberg). If
  nothing is found, the page is labeled license "unknown" — never
  assumed open by default.
- **Data pipeline** (`pipeline/`): cleans HTML into text, filters out
  low-quality text (mostly-boilerplate/repeated lines, garbled
  encoding, absurdly long "words") via dependency-free heuristics,
  optionally filters by detected language, **redacts personal data**
  (emails, phone numbers, IBANs, IPs, credit-card-like numbers via
  regex, with Luhn-checksum validation and citation-keyword awareness
  so DOIs/ISSNs/patent numbers in encyclopedic text aren't misflagged
  as credit cards or phone numbers; optionally also names and places
  via NER, see below),
  deduplicates content (both exact hashing and near-duplicates via
  SimHash, matched through a banded LSH index so lookups don't scan the
  whole corpus per document), and writes each record to JSONL with full provenance (URL,
  domain, license, language, quality score, collection timestamp).
- **Human review** (`pipeline/review.py`, `main.py review` command):
  walks through the produced dataset and has a human approve/reject
  each record, saving progress so you can stop and resume. Produces
  `*_approved.jsonl` and `*_rejected.jsonl`.
- **CLI** (`main.py`): `run` (scraping + pipeline), `review` (human
  review), and `stats` (record count, word-count and text-quality
  distribution, top domains, domain concentration, language mix,
  license mix, PII redaction totals, residual TDM reservations for an
  existing dataset) commands, driven by YAML config / options.

### Phase 2 — Fine-tuning (scaffold, not yet run end-to-end)

`training/` directory: dataset prep and a LoRA fine-tuning script for an
open source base model. See `training/README.md` for details — **don't
run it on unreviewed data**.

## PII: two layers

1. **Regex (always on)**: emails, phone numbers, IBANs, IPs,
   credit-card-like numbers — structural patterns, fast, no heavy
   dependencies.
2. **NER (optional)**: proper names and places in natural language, via
   spaCy. Off by default because it's a heavy dependency to install
   separately:
   ```bash
   pip install spacy
   python -m spacy download en_core_web_sm
   ```
   then set `output.use_ner: true` in `config/sources.yaml`. If spaCy or
   the model aren't installed, the system falls back on its own to
   regex-only redaction (with a log warning), without breaking.

Neither layer is an absolute guarantee, especially on higher-risk
sources (forums, comments, user-generated content): those always need a
human review pass (`main.py review`) before use.

### Evaluation — is the model actually "aware"?

`eval/` has 18 prompts across 6 categories (data provenance, consent,
privacy, misuse resistance, transparency, bias/limitations) to run
against a trained model and read the answers against a rubric — no
automatic score, this is meant to be judged by a human. See
`eval/README.md`.

## EU legal framework

This pipeline's design choices (PII redaction, license detection,
TDM opt-out handling, provenance tracking, human review) are grounded in
specific GDPR, EU Copyright Directive, and AI Act provisions relevant to
scraping data for AI training — not just generic "be ethical" advice.
See **[COMPLIANCE.md](COMPLIANCE.md)** for the actual articles, what's
automated, and what still needs a human (this is not legal advice).

`compliance/generate_training_summary.py` also drafts the web-scraped-data
part of the AI Act's mandatory training-data summary (Art. 53(1)(d)) from
your dataset export.

## What it doesn't do yet (possible next steps)

- An actual training run validated on real hardware, and an eval pass
  using `eval/` against the result (the scaffolds are there and tested
  in isolation, but not run together end-to-end).

## Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

If creating the virtualenv gives you trouble in your environment, a
direct `pip install -r requirements.txt` (no venv) also works, at the
cost of installing dependencies system/user-wide.

## Usage

### 1. Configure and run the scraper

```bash
cp config/sources.example.yaml config/sources.yaml
```

Edit `config/sources.yaml`:
- **`project.user_agent`**: put a real contact (email or project URL).
  The tool refuses to start with the example placeholder user agent.
- **`seeds`**: the starting URLs.
- **`deny_domains`**: domains to always skip.
- **`output.min_license_confidence`**: `null` to keep everything (with a
  license label), `"high"` to keep only content with a clearly
  identified license.
- **`output.near_duplicate_threshold`**: fuzzy dedup threshold (default
  8; see the comments in the file).
- **`output.use_ner`** / **`ner_model`**: turn on advanced PII redaction
  (requires spaCy, see above).

```bash
python main.py run --config config/sources.yaml --verbose
```

Interrupted mid-run? Just run the same command again: pages already
fetched (successfully or not, robots-blocked, deny-listed, or wrong
content type) are skipped automatically, tracked in a
`*.crawl_state.json` file next to the dataset. Pass `--reset-state` to
ignore it and start over.

```bash
python main.py stats --dataset dataset/output.jsonl
```

Quick summary of a dataset already produced: record count, word-count
distribution, top domains, license mix, PII redaction totals, and how
many records still carry a TDM reservation flag.

The dataset is written as JSONL to `dataset/output.jsonl` (path
configurable). Each line is a record with this schema:

```json
{
  "id": "uuid",
  "url": "https://...",
  "domain": "example.com",
  "title": "...",
  "text": "cleaned text, with PII redacted",
  "word_count": 123,
  "content_hash": "sha256 of the normalized text",
  "license": "CC BY-SA 4.0",
  "license_source": "rel_license_link",
  "license_confidence": "high",
  "tdm_reservation": false,
  "language": "en",
  "quality_score": 0.87,
  "pii_redactions": {"EMAIL": 2},
  "fetched_at": 1234567890.0,
  "collected_at": 1234567890.0
}
```

`language` is `null` unless the optional `langdetect` package is
installed (see `requirements.txt`) -- without it, no language filtering
happens either, just an informational `null`. `quality_score` (0-1,
higher is better) is always computed from dependency-free noise
heuristics (`pipeline/quality_filter.py`) regardless of whether
`output.use_quality_filter` actually drops low-scoring pages.

### 2. Review the dataset

```bash
python main.py review --dataset dataset/output.jsonl
```

Shows you one record at a time (URL, license, PII redactions, text
preview) and asks `[a]pprove / [r]eject / [s]kip / [q]uit`. Decisions
are saved to disk immediately: you can stop and resume anytime, already
decided records won't be shown again. Result:
`dataset/output_approved.jsonl` and `dataset/output_rejected.jsonl`.

### 3. (Optional) Phase 2 — fine-tuning

See `training/README.md`. In short:

```bash
python training/prepare_dataset.py --input dataset/output_approved.jsonl --output-dir training/data
pip install -r training/requirements-training.txt
cp training/config.example.yaml training/config.yaml   # customize base_model etc.
python training/train_lora.py --config training/config.yaml --check   # validate without downloading anything
python training/train_lora.py --config training/config.yaml           # actual training
```

## Ethical/legal checklist before adding a source

Before adding a seed to `config/sources.yaml`, ask yourself:

1. **Does the site allow it?** Check `robots.txt` and the site's Terms
   of Service, not just whether `robots.txt` technically blocks the
   page.
2. **What license does the content have?** If it's unclear, consider
   setting `min_license_confidence: "high"` to automatically exclude
   that source from the final dataset, even if it's still fetched for
   inspection.
3. **Is there personal data?** The automatic filters (regex + optional
   NER) are a first line of defense, not a guarantee. For higher-risk
   sources (forums, social media, comments) always run `main.py review`
   before treating the data as training-ready.
4. **Has the site asked not to be scraped?** If an owner reaches out
   asking to be excluded, add them to `deny_domains` right away.
5. **Do you actually need it?** Prefer sources with an explicit open
   license (Wikipedia, Project Gutenberg, public datasets, open source
   repositories) when the goal is a reusable, redistributable dataset.
6. **If you're in/targeting the EU:** see [COMPLIANCE.md](COMPLIANCE.md)
   for how GDPR, the Copyright Directive's TDM exception, and the AI Act
   bear on this specific step.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

147 tests cover the pure logic of the scraper (including retry/backoff
and Content-Type filtering, with `requests.get` mocked), pipeline
(including the text-quality heuristics, language-detection degradation
path, and a correctness check of the banded LSH dedup index against a
brute-force reference), crawl resumability, review, and training data prep —
none require network access or heavy dependencies (torch/spaCy/
langdetect aren't needed for them to pass; the code degrades correctly
when those aren't installed).

An actual scraping run (`python main.py run --config ...`) does need
outbound network access to the target sites: running it from an
environment with restricted egress will produce connection errors even
though the logic itself is correct.

## Project structure

```
ScrapeLLM/
├── scraper/            # ethical fetching: robots.txt, rate limit, license
│   ├── robots.py
│   ├── rate_limiter.py
│   ├── license_detector.py
│   ├── tdm_rights.py    # Art. 4(3) Directive 2019/790 opt-out detection (TDMRep)
│   ├── fetcher.py        # retry-with-backoff + Content-Type filtering
│   ├── crawl_state.py    # resumable crawling (persisted visited-URL state)
│   └── crawler.py
├── pipeline/            # cleaning, PII, dedup, review, dataset writing
│   ├── text_extractor.py
│   ├── pii_filter.py
│   ├── ner_pii.py        # advanced PII via NER (optional, spaCy)
│   ├── dedup.py           # exact hash + SimHash/LSH index for near-duplicates
│   ├── review.py          # human review (persisted state)
│   ├── quality_filter.py  # dependency-free text-quality heuristics
│   ├── language_detector.py # optional language detection (langdetect)
│   ├── dataset_stats.py   # summary stats for `main.py stats`
│   └── dataset_writer.py
├── training/             # phase 2: LoRA fine-tuning (scaffold)
│   ├── prepare_dataset.py
│   ├── train_lora.py
│   ├── config.example.yaml
│   ├── requirements-training.txt
│   └── README.md
├── eval/                 # data-awareness evaluation prompts + runner
│   ├── prompts.jsonl
│   ├── run_eval.py
│   └── README.md
├── compliance/           # AI Act training-data-summary draft generator
│   └── generate_training_summary.py
├── COMPLIANCE.md          # GDPR / AI Act / Copyright Directive mapping (+ .it.md)
├── config/
│   └── sources.example.yaml
├── dataset/              # JSONL output (git-ignored)
├── tests/                # 147 unit tests, no heavy dependencies
├── main.py               # CLI: run, review, stats
├── requirements.txt
└── LICENSE                # MIT
```

## License

MIT — see `LICENSE`.
