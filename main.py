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

import logging
import sys

import click
import yaml

from scraper.fetcher import EthicalFetcher
from scraper.crawler import Crawler
from pipeline.dataset_writer import DatasetWriter
from pipeline.review import ReviewSession


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


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
def run(config_path: str, verbose: bool):
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
    )
    crawler = Crawler(
        fetcher=fetcher,
        max_pages_per_domain=crawl_cfg.get("max_pages_per_domain", 20),
        follow_links=crawl_cfg.get("follow_links", False),
    )
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
        for result in crawler.crawl_seed(seed):
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


if __name__ == "__main__":
    cli()
