#!/usr/bin/env python3
"""ScrapeLLM CLI.

Uso tipico:
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
import sys

import click
import yaml

from scraper.fetcher import EthicalFetcher
from scraper.crawler import Crawler
from scraper.crawl_state import CrawlState
from pipeline.dataset_writer import DatasetWriter
from pipeline.review import ReviewSession
from pipeline.dataset_stats import compute_stats, render_text as render_stats_text


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


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
@click.option("--config", "config_path", required=True, help="Percorso al file YAML di configurazione")
@click.option("--verbose", is_flag=True, help="Log dettagliati")
@click.option("--reset-state", is_flag=True,
              help="Ignora lo stato di crawling salvato in precedenza e riparti da zero")
def run(config_path: str, verbose: bool, reset_state: bool):
    """Esegue scraping + pipeline secondo il file di configurazione."""
    setup_logging(verbose)
    logger = logging.getLogger("scrapellm.cli")

    cfg = load_config(config_path)

    project = cfg.get("project", {})
    output_cfg = cfg.get("output", {})
    politeness_cfg = cfg.get("politeness", {})
    crawl_cfg = cfg.get("crawl", {})
    seeds = cfg.get("seeds", [])
    deny_domains = cfg.get("deny_domains", [])

    user_agent = project.get("user_agent")
    if not user_agent or "tuo-email@example.com" in user_agent:
        logger.error(
            "Configura un User-Agent onesto con un contatto reale in "
            "'project.user_agent' prima di lanciare uno scraping vero."
        )
        sys.exit(1)

    if not seeds:
        logger.error("Nessun seed URL trovato in 'seeds'. Niente da fare.")
        sys.exit(1)

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
                   f"PII redatte: {record.get('pii_redactions') or 'nessuna'}")
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
