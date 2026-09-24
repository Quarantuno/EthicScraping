#!/usr/bin/env python3
"""ScrapeLLM CLI.

Uso tipico:
    python main.py init                          # crea config/sources.yaml guidato
    python main.py run --config config/sources.yaml
    python main.py run --config config/sources.yaml --verbose

Filosofia del progetto: raccogliere dati per addestrare AI in modo
consapevole significa, prima di tutto, essere consapevoli noi di come
li raccogliamo. Ogni fetch rispetta robots.txt, applica rate limiting,
e ogni record del dataset porta con se' provenienza e informazioni
sulla licenza -- vedi README.md per la checklist etica completa.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys

import click
import yaml

from scraper.fetcher import EthicalFetcher
from scraper.crawler import Crawler
from scraper.crawl_state import CrawlState
from scraper.sitemap import SitemapFetcher
from pipeline.dataset_writer import DatasetWriter
from pipeline.dedup import Deduplicator
from pipeline.review import ReviewSession
from pipeline.dataset_stats import compute_stats, render_text as render_stats_text


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_config(cfg: dict) -> list[str]:
    """Upfront validation of a parsed sources.yaml config.

    Returns a list of human-readable error strings (empty if the config
    looks usable). Shared between `init` (to confirm what it just wrote
    is actually usable) and `run` (to fail fast with a clear message
    instead of a confusing exception deep inside the pipeline).
    """
    errors: list[str] = []
    if not isinstance(cfg, dict):
        return ["Il file di configurazione non contiene un oggetto YAML valido."]

    project = cfg.get("project") or {}
    user_agent = project.get("user_agent")
    if not user_agent:
        errors.append(
            "Manca 'project.user_agent': serve un User-Agent onesto con un "
            "contatto reale (email o URL del progetto)."
        )
    elif "tuo-email@example.com" in user_agent:
        errors.append(
            "'project.user_agent' e' ancora il placeholder dell'esempio: "
            "mettici un contatto reale prima di lanciare uno scraping vero."
        )

    seeds = cfg.get("seeds")
    if not seeds:
        errors.append("Nessun seed URL trovato in 'seeds': non c'e' niente da scaricare.")
    elif not isinstance(seeds, list):
        errors.append("'seeds' deve essere una lista di URL.")
    else:
        for s in seeds:
            if not isinstance(s, str) or not s.startswith(("http://", "https://")):
                errors.append(f"Seed non valido (deve essere un URL http/https): {s!r}")

    deny_domains = cfg.get("deny_domains")
    if deny_domains is not None and not isinstance(deny_domains, list):
        errors.append("'deny_domains' deve essere una lista (anche vuota: []).")

    crawl_cfg = cfg.get("crawl") or {}
    max_pages = crawl_cfg.get("max_pages_per_domain")
    if max_pages is not None and (not isinstance(max_pages, int) or max_pages <= 0):
        errors.append("'crawl.max_pages_per_domain' deve essere un intero positivo.")

    return errors


# --- Personalizzazione di config/sources.example.yaml per `init` ----------
#
# Sostituzioni testuali mirate invece di un giro completo yaml.safe_load +
# yaml.dump: l'esempio e' pieno di commenti esplicativi che un dump
# perderebbe silenziosamente. Se il testo dell'esempio cambia senza
# aggiornare questi pattern, la sostituzione semplicemente non scatta
# (nessun errore) -- da qui il test dedicato in tests/test_cli.py che
# verifica che la sostituzione avvenga davvero.

_EXAMPLE_PROJECT_NAME_RE = re.compile(r'name:\s*"esempio-dataset"')
_EXAMPLE_UA_RE = re.compile(
    r'user_agent:\s*"ScrapeLLM-Bot/0\.1 \(\+https://github\.com/tuo-utente/ScrapeLLM; '
    r'contatto: tuo-email@example\.com\)"'
)
_EXAMPLE_SEEDS_BLOCK_RE = re.compile(
    r"seeds:\n"
    r"  - https://it\.wikipedia\.org/wiki/Intelligenza_artificiale\n"
    r"  - https://it\.wikipedia\.org/wiki/Etica_dei_dati\n"
)


def _render_config_from_example(example_text: str, project_name: str | None,
                                 user_agent: str | None, first_seed: str | None) -> str:
    """Returns example_text with project name / user agent / first seed
    substituted in, keeping every comment line untouched. Any argument
    left as None/empty skips that substitution (defaults from the
    example are kept as-is)."""
    text = example_text
    if project_name:
        text = _EXAMPLE_PROJECT_NAME_RE.sub(f'name: "{project_name}"', text, count=1)
    if user_agent:
        escaped = user_agent.replace('"', '\\"')
        text = _EXAMPLE_UA_RE.sub(f'user_agent: "{escaped}"', text, count=1)
    if first_seed:
        text = _EXAMPLE_SEEDS_BLOCK_RE.sub(f"seeds:\n  - {first_seed}\n", text, count=1)
    return text


def _default_state_path(dataset_path: str) -> str:
    """Derives a crawl-state path alongside the dataset, e.g.
    'dataset/output.jsonl' -> 'dataset/output.crawl_state.json'."""
    base, _ext = os.path.splitext(dataset_path)
    return f"{base}.crawl_state.json"


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@click.group()
def cli():
    """ScrapeLLM: pipeline di scraping etico e costruzione dataset."""


@cli.command()
@click.option("--config", "config_path", default="config/sources.yaml",
              help="Percorso dove scrivere il file di configurazione (default: config/sources.yaml)")
@click.option("--example", "example_path", default="config/sources.example.yaml",
              help="Percorso del file di esempio da cui partire")
@click.option("--force", is_flag=True, help="Sovrascrive il file di destinazione se esiste gia'")
@click.option("--non-interactive", is_flag=True,
              help="Copia l'esempio cosi' com'e' senza fare domande (utile da script/CI)")
def init(config_path: str, example_path: str, force: bool, non_interactive: bool):
    """Crea config/sources.yaml partendo dall'esempio, con qualche domanda
    rapida per personalizzarlo (nome progetto, User-Agent, primo seed).

    Le righe di commento esplicative dell'esempio vengono mantenute cosi'
    come sono: il file scritto resta pensato per essere rifinito a mano.
    """
    if not os.path.exists(example_path):
        click.echo(f"File di esempio non trovato: {example_path}", err=True)
        sys.exit(1)

    if os.path.exists(config_path) and not force:
        click.echo(
            f"{config_path} esiste gia'. Usa --force per sovrascriverlo, "
            "o modificalo a mano.", err=True,
        )
        sys.exit(1)

    with open(example_path, "r", encoding="utf-8") as f:
        example_text = f.read()

    if non_interactive:
        project_name = user_agent = first_seed = None
    else:
        click.echo("Qualche domanda rapida per personalizzare config/sources.yaml")
        click.echo("(premi Invio per tenere il default suggerito tra parentesi).\n")
        project_name = click.prompt("Nome del progetto/dataset", default="esempio-dataset")
        click.echo(
            "\nUser-Agent: identificati onestamente con un contatto reale "
            "(email o URL del progetto) -- un User-Agent anonimo o che finge "
            "di essere un browser e' una pratica scorretta verso i siti che visiti."
        )
        default_ua = ('ScrapeLLM-Bot/0.1 (+https://github.com/tuo-utente/ScrapeLLM; '
                      'contatto: tuo-email@example.com)')
        user_agent = click.prompt("User-Agent completo", default=default_ua)
        first_seed = click.prompt(
            "URL del primo seed da cui partire (vuoto = tieni gli esempi predefiniti)",
            default="", show_default=False,
        )

    new_text = _render_config_from_example(example_text, project_name, user_agent, first_seed)

    dest_dir = os.path.dirname(config_path)
    if dest_dir:
        os.makedirs(dest_dir, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(new_text)

    click.echo(f"\nScritto {config_path}.")

    cfg = yaml.safe_load(new_text)
    errors = validate_config(cfg)
    if errors:
        click.echo("\nDa sistemare a mano prima di lanciare 'run':")
        for e in errors:
            click.echo(f"  - {e}")
    else:
        click.echo(f"Configurazione valida. Puoi lanciare: python main.py run --config {config_path}")


@cli.command("discover-seeds")
@click.option("--sitemap", "sitemap_url", required=True,
              help="URL della sitemap (anche un sitemap index, cioe' una sitemap che elenca altre sitemap)")
@click.option("--config", "config_path", default=None,
              help="Config YAML da cui leggere lo User-Agent onesto (project.user_agent), se non passato con --user-agent")
@click.option("--user-agent", "user_agent", default=None, help="User-Agent onesto da usare per il fetch")
@click.option("--limit", type=int, default=None, help="Numero massimo di URL da mostrare/scrivere")
@click.option("--contains", "contains_filter", default=None,
              help="Tieni solo gli URL che contengono questa sottostringa (es. '/blog/')")
@click.option("--output", "output_path", default=None,
              help="Se indicato, scrive gli URL trovati come blocco 'seeds:' YAML in questo file invece di stamparli")
@click.option("--verbose", is_flag=True, help="Log dettagliati")
def discover_seeds(sitemap_url: str, config_path: str, user_agent: str, limit: int,
                    contains_filter: str, output_path: str, verbose: bool):
    """Scopre URL candidati come seed leggendo una sitemap.xml.

    Non tocca config/sources.yaml automaticamente: stampa una lista (o la
    scrive su file con --output) che poi scegli tu cosa tenere e incollare
    a mano in 'seeds:'. Rispetta comunque robots.txt e il rate limiting
    come ogni altro fetch di questo progetto -- una sitemap index puo'
    elencare decine di sotto-sitemap.
    """
    setup_logging(verbose)
    logger = logging.getLogger("scrapellm.cli")

    if not user_agent and config_path:
        cfg = load_config(config_path)
        user_agent = (cfg.get("project") or {}).get("user_agent")
    if not user_agent:
        click.echo(
            "Serve un User-Agent onesto: passalo con --user-agent, oppure "
            "--config per leggerlo da project.user_agent.", err=True,
        )
        sys.exit(1)
    if "tuo-email@example.com" in user_agent:
        click.echo("Quello User-Agent e' ancora il placeholder dell'esempio.", err=True)
        sys.exit(1)

    fetcher = SitemapFetcher(user_agent=user_agent)
    logger.info("Scarico la sitemap da %s ...", sitemap_url)
    urls = fetcher.discover(sitemap_url)
    logger.info("Trovati %d URL nella sitemap.", len(urls))

    if contains_filter:
        urls = [u for u in urls if contains_filter in u]
    if limit:
        urls = urls[:limit]

    if not urls:
        click.echo("Nessun URL trovato (o tutti scartati dal filtro/robots.txt).")
        return

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("seeds:\n")
            for u in urls:
                f.write(f"  - {u}\n")
        click.echo(f"Scritti {len(urls)} URL in {output_path}.")
    else:
        click.echo(f"{len(urls)} URL trovati:\n")
        for u in urls:
            click.echo(f"  - {u}")


@cli.command()
@click.option("--config", "config_path", required=True, help="Percorso al file YAML di configurazione")
@click.option("--verbose", is_flag=True, help="Log dettagliati")
@click.option("--reset-state", is_flag=True,
              help="Ignora lo stato di crawling salvato in precedenza e riparti da zero")
def run(config_path: str, verbose: bool, reset_state: bool):
    """Esegue scraping + pipeline secondo il file di configurazione."""
    setup_logging(verbose)
    logger = logging.getLogger("scrapellm.cli")

    cfg = load_config(config_path)

    errors = validate_config(cfg)
    if errors:
        for e in errors:
            logger.error(e)
        logger.error("Puoi generare una configurazione di partenza valida con 'python main.py init'.")
        sys.exit(1)

    project = cfg.get("project", {})
    output_cfg = cfg.get("output", {})
    politeness_cfg = cfg.get("politeness", {})
    crawl_cfg = cfg.get("crawl", {})
    seeds = cfg.get("seeds", [])
    deny_domains = cfg.get("deny_domains", [])

    user_agent = project.get("user_agent")

    fetcher = EthicalFetcher(
        user_agent=user_agent,
        deny_domains=deny_domains,
        default_delay=politeness_cfg.get("default_delay_seconds", 2.0),
        timeout=politeness_cfg.get("timeout_seconds", 15),
        check_tdm_reservation=output_cfg.get("respect_tdm_optout", True),
        max_retries=politeness_cfg.get("max_retries", 2),
        backoff_seconds=politeness_cfg.get("backoff_seconds", 2.0),
    )
    crawler = Crawler(
        fetcher=fetcher,
        max_pages_per_domain=crawl_cfg.get("max_pages_per_domain", 20),
        follow_links=crawl_cfg.get("follow_links", False),
    )

    if "state_path" in crawl_cfg:
        state_path = crawl_cfg["state_path"] or None
    else:
        state_path = _default_state_path(output_cfg.get("dataset_path", "dataset/output.jsonl"))
    if reset_state and state_path and os.path.exists(state_path):
        os.remove(state_path)
        logger.info("Stato di crawling resettato (rimosso %s).", state_path)
    crawl_state = CrawlState(state_path)
    writer = DatasetWriter(
        output_path=output_cfg.get("dataset_path", "dataset/output.jsonl"),
        min_license_confidence=output_cfg.get("min_license_confidence"),
        min_word_count=output_cfg.get("min_word_count", 50),
        near_duplicate_threshold=output_cfg.get("near_duplicate_threshold", 8),
        use_ner=output_cfg.get("use_ner", False),
        ner_model=output_cfg.get("ner_model", "it_core_news_sm"),
        respect_tdm_optout=output_cfg.get("respect_tdm_optout", True),
        use_quality_filter=output_cfg.get("use_quality_filter", True),
        min_alpha_ratio=output_cfg.get("min_alpha_ratio", 0.5),
        min_unique_line_ratio=output_cfg.get("min_unique_line_ratio", 0.4),
        max_long_word_ratio=output_cfg.get("max_long_word_ratio", 0.05),
        use_language_filter=output_cfg.get("use_language_filter", False),
        allowed_languages=output_cfg.get("allowed_languages"),
    )

    logger.info("Avvio scraping di %d seed -> %s", len(seeds), writer.output_path)

    for seed in seeds:
        logger.info("Seed: %s", seed)
        for result in crawler.crawl_seed(seed, state=crawl_state):
            if result.status == "ok":
                record = writer.process(result)
                if record:
                    logger.info(
                        "OK  %s  (%d parole, licenza=%s/%s)",
                        result.url, record["word_count"],
                        record["license"], record["license_confidence"],
                    )
                else:
                    logger.info("SCARTATO %s", result.url)
            else:
                logger.warning("SALTATO %s (%s)%s", result.url, result.status,
                                f": {result.error}" if result.error else "")

    logger.info("Fine. Statistiche: %s", writer.stats)
    if crawl_state.state_path:
        logger.info(
            "Stato di crawling salvato in %s (%d URL totali visitati finora).",
            crawl_state.state_path, len(crawl_state.visited),
        )


@cli.command()
@click.option("--inputs", "input_paths", multiple=True, required=True,
              help="File JSONL da unire (passa --inputs piu' volte, uno per file)")
@click.option("--output", "output_path", required=True, help="Percorso del file JSONL unito")
@click.option("--near-duplicate-threshold", type=int, default=8, show_default=True,
              help="Soglia di distanza di Hamming per la dedup fuzzy (come output.near_duplicate_threshold nel config)")
@click.option("--exact-only", is_flag=True,
              help="Deduplica solo per hash esatto, disattiva il controllo fuzzy (SimHash)")
@click.option("--no-dedup", is_flag=True, help="Nessuna deduplica: concatena e basta")
def merge(input_paths: tuple, output_path: str, near_duplicate_threshold: int,
          exact_only: bool, no_dedup: bool):
    """Unisce piu' dataset JSONL gia' prodotti in uno solo, deduplicando
    tra TUTTI i file insieme (non solo dentro ciascuno) con lo stesso
    meccanismo usato da 'run' -- hash esatto + near-duplicate via
    SimHash (pipeline.dedup.Deduplicator).

    Utile per unire run fatte in momenti diversi, o dataset costruiti
    da seed set diversi, senza ritrovarsi lo stesso articolo due volte
    solo perche' e' finito in due file.
    """
    if len(input_paths) < 2:
        click.echo("Servono almeno due file in --inputs per fare un merge.", err=True)
        sys.exit(1)

    for path in input_paths:
        if not os.path.exists(path):
            click.echo(f"File non trovato: {path}", err=True)
            sys.exit(1)

    threshold = None if (no_dedup or exact_only) else near_duplicate_threshold
    dedup = None if no_dedup else Deduplicator(near_duplicate_threshold=threshold)

    total_in = 0
    total_dupes = 0
    per_file_kept = []

    dest_dir = os.path.dirname(output_path)
    if dest_dir:
        os.makedirs(dest_dir, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as out_f:
        for path in input_paths:
            kept_here = 0
            with open(path, "r", encoding="utf-8") as in_f:
                for line in in_f:
                    line = line.strip()
                    if not line:
                        continue
                    record = json.loads(line)
                    total_in += 1
                    if dedup is not None and dedup.is_duplicate(record.get("text", "")):
                        total_dupes += 1
                        continue
                    out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    kept_here += 1
            per_file_kept.append((path, kept_here))

    total_out = total_in - total_dupes
    click.echo(f"Letti {total_in} record da {len(input_paths)} file.")
    if dedup is not None:
        mode = "solo hash esatto" if threshold is None else f"hash esatto + fuzzy (soglia={threshold})"
        click.echo(f"Scartati {total_dupes} duplicati/quasi-duplicati ({mode}).")
    click.echo(f"Scritti {total_out} record in {output_path}.")
    for path, kept in per_file_kept:
        click.echo(f"  {path}: {kept} tenuti")


@cli.command()
@click.option("--dataset", "dataset_path", required=True, help="Percorso al file JSONL da revisionare")
@click.option("--limit", type=int, default=None, help="Rivedi al massimo N record in questa sessione")
@click.option("--preview-chars", type=int, default=600, help="Quanti caratteri di testo mostrare per record")
def review(dataset_path: str, limit: int, preview_chars: int):
    """Revisione umana interattiva dei record di un dataset gia' prodotto.

    Le decisioni vengono salvate subito su disco: puoi interrompere con
    Ctrl+C o 'q' e riprendere in seguito, i record gia' decisi non
    verranno riproposti.
    """
    session = ReviewSession(dataset_path)
    pending = session.pending()

    if not pending:
        click.echo(f"Nessun record da revisionare. Statistiche: {session.stats()}")
        return

    if limit:
        pending = pending[:limit]

    click.echo(f"{len(pending)} record da revisionare in questa sessione "
               f"(totale dataset: {session.stats()['total']}).")
    click.echo("Comandi: [a]pprova  [r]ifiuta  [s]alta  [q]uit\n")

    for i, record in enumerate(pending, start=1):
        click.echo("-" * 70)
        click.echo(f"[{i}/{len(pending)}] {record.get('url')}")
        click.echo(f"Titolo: {record.get('title')}")
        click.echo(f"Licenza: {record.get('license')} "
                   f"(confidenza: {record.get('license_confidence')})")
        click.echo(f"Parole: {record.get('word_count')}  "
                   f"Lingua: {record.get('language') or 'sconosciuta'}  "
                   f"Qualita': {record.get('quality_score', 'n/d')}")
        click.echo(f"PII redatte: {record.get('pii_redactions') or 'nessuna'}")
        text = record.get("text", "")
        click.echo(f"\n{text[:preview_chars]}"
                   f"{'...' if len(text) > preview_chars else ''}\n")

        choice = click.prompt("Decisione", type=click.Choice(
            ["a", "r", "s", "q"], case_sensitive=False), default="s")

        if choice == "q":
            click.echo("Interrotto dall'utente.")
            break
        if choice == "s":
            continue
        session.decide(record, "approved" if choice == "a" else "rejected")

    click.echo("-" * 70)
    click.echo(f"Fine sessione. Statistiche: {session.stats()}")
    click.echo(f"Approvati -> {session.approved_path}")
    click.echo(f"Rifiutati -> {session.rejected_path}")


@cli.command()
@click.option("--dataset", "dataset_path", required=True, help="Percorso al file JSONL da analizzare")
@click.option("--top-domains", type=int, default=10, help="Quanti domini principali mostrare")
@click.option("--top-percent", type=float, default=10.0,
              help="Percentuale di domini principali da considerare 'top' (10 default, 5 se SME)")
def stats(dataset_path: str, top_domains: int, top_percent: float):
    """Statistiche rapide su un dataset JSONL gia' prodotto: numero di
    record, distribuzione delle parole, domini principali, licenze,
    PII redatte e reservation TDM residue.
    """
    records = []
    with open(dataset_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    if not records:
        click.echo(f"Nessun record trovato in {dataset_path}.")
        return

    result = compute_stats(records, top_percent=top_percent)
    click.echo(render_stats_text(result, top_n_domains=top_domains))


if __name__ == "__main__":
    cli()
