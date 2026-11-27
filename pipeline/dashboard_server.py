"""Loopback-only controls for adding crawl seeds and running the existing CLI."""
import json
import os
import secrets
import subprocess
import sys
import tempfile
import webbrowser
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import yaml

from .review import ReviewSession
from .stats_html import render_dashboard


def _seed_url(value):
    if "://" not in value:
        value = "https://" + value
    if any(char.isspace() for char in value):
        raise ValueError("Inserisci un dominio o un URL senza spazi.")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None:
        raise ValueError("Inserisci un URL HTTP o HTTPS senza credenziali.")
    return parsed._replace(fragment="").geturl()


def _read_config(path):
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Il file di configurazione deve contenere un oggetto YAML.")
    seeds = config.setdefault("seeds", [])
    if not isinstance(seeds, list) or any(not isinstance(seed, str) for seed in seeds):
        raise ValueError("La configurazione deve contenere una lista di seed URL.")
    return config


def _save_config(path, config):
    backup = path.with_name(path.name + ".dashboard.bak")
    try:
        with os.fdopen(os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                       "w", encoding="utf-8") as copy:
            copy.write(path.read_text(encoding="utf-8"))
    except FileExistsError:
        pass
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     delete=False) as temporary:
        pending = Path(temporary.name)
    try:
        pending.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def _change_seed(path, value, remove=False):
    config = _read_config(path)
    seeds = config["seeds"]
    if remove:
        if value not in seeds:
            return "Seed non trovato."
        seeds.remove(value)
        message = "Seed rimosso dalla configurazione."
    else:
        value = _seed_url(value)
        if value in seeds:
            return "Il seed è già presente."
        seeds.append(value)
        message = "Seed aggiunto alla configurazione."
    _save_config(path, config)
    return message


def _home(config, token, message, running):
    disabled = " disabled" if running else ""
    run_disabled = " disabled" if running or not config["seeds"] else ""
    refresh = '<meta http-equiv="refresh" content="3">' if running else ""
    seeds = "".join(
        f'<li><span>{escape(seed)}</span><form method="post" action="/remove-seed">'
        f'<input type="hidden" name="token" value="{token}">'
        f'<input type="hidden" name="seed" value="{escape(seed)}">'
        f'<button class="secondary"{disabled}>Rimuovi seed</button></form></li>'
        for seed in config["seeds"]
    ) or "<li>Nessun seed aggiunto.</li>"
    return f"""<!doctype html><html lang="it"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{refresh}<title>Pannello di raccolta · EthicScraping</title>
<style>
  :root {{ font: 16px/1.5 system-ui,sans-serif; color:#162c28; background:#eef4f0 }}
  main {{ max-width:58rem; margin:auto; padding:clamp(1rem,4vw,3rem) }}
  header,section {{ padding:1.3rem; border-radius:1rem; margin-bottom:1rem }}
  header {{ background:#123d33;color:white }}
  header p,h1,h2 {{ margin:0 0 .45rem }}
  section {{ background:white;border:1px solid #cee0d6 }}
  input,button,.button {{ font:inherit;padding:.5rem .75rem;border-radius:.4rem }}
  input {{ border:1px solid #769287;box-sizing:border-box;width:100%;margin:.25rem 0 .65rem }}
  button,.button {{ display:inline-block;background:#175744;color:white;border:0;cursor:pointer }}
  .actions {{ display:flex;flex-wrap:wrap;gap:.6rem }}
  li {{ overflow-wrap:anywhere; margin:.5rem 0 }}
  li form {{ display:inline-block; margin-left:.5rem }}
  .secondary {{ background:#e1efe7;color:#174536 }}
  button:disabled {{ opacity:.55;cursor:default }}
  pre {{ color:#193d33;white-space:pre-wrap;overflow-wrap:anywhere }}
</style>
<main><header><p>EthicScraping</p><h1>Pannello di raccolta</h1></header>
<section><h2>Fonti</h2><form method="post" action="/seed">
<input type="hidden" name="token" value="{token}">
<label for="seed">Dominio o URL iniziale</label>
<input id="seed" name="seed" required maxlength="4096" placeholder="https://example.org/articoli"{disabled}>
<button{disabled}>Aggiungi seed</button></form><ul>{seeds}</ul></section>
<section><h2>Raccolta e controlli</h2><div class="actions">
<form method="post" action="/run"><input type="hidden" name="token" value="{token}">
<button{run_disabled}>Avvia raccolta</button></form>
<a class="button" href="/report">Apri statistiche e controlli</a>
<a class="button secondary" href="/review">Rivedi i record</a></div>
<p>Il report verifica il dataset salvato. La raccolta può richiedere alcuni minuti.</p>
<pre role="status">{escape(message)}</pre></section></main></html>"""


def _dataset_path(config):
    return Path((config.get("output") or {}).get("dataset_path", "dataset/output.jsonl"))


def _review_page(session, token, after=""):
    pending = [record for record in session.pending() if isinstance(record.get("id"), str)
               and record["id"]]
    if after:
        index = next((index for index, record in enumerate(pending)
                      if record["id"] == after), -1)
        pending = pending[index + 1:]
    stats = session.stats()
    summary = (f'{stats["approved"]} approvati · {stats["rejected"]} rifiutati'
               f' · {stats["pending"]} da rivedere')
    body = '<p>Nessun record da rivedere.</p>'
    if not pending and after:
        body = '<p>Fine della lista.</p><a href="/review">Riparti dai record in attesa</a>'
    elif not pending and stats["pending"]:
        body = '<p>I record rimasti richiedono un identificativo valido per essere rivisti.</p>'
    if pending:
        record = pending[0]
        title = escape(str(record.get("title") or "Record senza titolo"))
        details = "".join(
            f'<dt>{label}</dt><dd>{escape(str(value))}</dd>'
            for label, value in (
                ("Provenienza", record.get("url") or "Sconosciuta"),
                ("Licenza", record.get("license") or "Sconosciuta"),
                ("Confidenza della licenza", record.get("license_confidence") or "Sconosciuta"),
                ("Riserva TDM", _signal_label(record.get("tdm_reservation"))),
                ("Opt-out noai", _signal_label(record.get("ai_optout"))),
                ("Redazioni di dati personali", record.get("pii_redactions") or "Nessuna registrata"),
            )
        )
        text = escape(str(record.get("text") or ""))
        record_id = escape(record["id"])
        skip = escape("/review?" + urlencode({"after": record["id"]}))
        body = f"""<article><h2>{title}</h2><dl>{details}</dl>
<h3>Testo raccolto</h3><pre tabindex="0">{text}</pre>
<form method="post" action="/review">
<input type="hidden" name="token" value="{token}">
<input type="hidden" name="record" value="{record_id}">
<button name="decision" value="approved">Approva</button>
<button class="secondary" name="decision" value="rejected">Rifiuta</button>
<a href="{skip}">Salta per ora</a>
</form></article>"""
    approved_report = (
        '<p><a href="/report/approved">Controlla il dataset approvato</a></p>'
        if session.approved_path.is_file() else ""
    )
    return f"""<!doctype html><html lang="it"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Revisione del dataset · EthicScraping</title>
<style>
  :root {{font:16px/1.5 system-ui,sans-serif;color:#162c28;background:#eef4f0}}
  main {{max-width:56rem;margin:auto;padding:clamp(1rem,4vw,3rem)}}
  header,article {{padding:1.3rem;border-radius:1rem;margin:1rem 0}}
  header {{background:#123d33;color:white}}
  article {{background:white;border:1px solid #cee0d6}}
  h1,h2 {{margin:0;overflow-wrap:anywhere}}
  dt {{font-weight:650}} dd {{margin:0 0 .5rem;overflow-wrap:anywhere}}
  pre {{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere;overflow:auto;
    max-height:55vh;padding:1rem;background:#f3f7f5;border-radius:.5rem}}
  form {{display:flex;flex-wrap:wrap;gap:.75rem;align-items:center}}
  button {{font:inherit;padding:.5rem .8rem;border:0;border-radius:.4rem;
    color:white;background:#175744;cursor:pointer}}
  .secondary {{background:#f4e7e1;color:#682f26}}
  a {{color:#175744}}
</style>
<main><a href="/">Torna al pannello di raccolta</a>
<header><h1>Revisione del dataset</h1><p>{summary}</p></header>{approved_report}{body}</main></html>"""


def _signal_label(value):
    if value is True:
        return "Rilevato"
    if value is False:
        return "Non rilevato"
    return "Verifica assente"


def serve(config_path, open_browser):
    config_path = config_path.resolve()
    _read_config(config_path)
    token = secrets.token_urlsafe(32)
    message = ""
    process = None
    result_file = None

    def update_status():
        nonlocal process, result_file, message
        if process is None or process.poll() is None:
            return
        message = "Raccolta interrotta. Controlla il terminale per i dettagli."
        try:
            if process.returncode == 0:
                result_file.seek(0)
                report = json.load(result_file)
                message = (
                    f'Raccolta terminata. Nuovi record: {report["pipeline"]["written"]}.\n'
                    f'Esiti richieste: {json.dumps(report["fetch_statuses"], ensure_ascii=False)}'
                )
        except (OSError, ValueError, TypeError, KeyError):
            message = "Raccolta terminata. Verifica il dataset e il terminale per i dettagli."
        finally:
            result_file.close()
            result_file = None
            process = None

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body, status=200):
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                             "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def _valid_host(self):
            return self.headers.get("Host") == address

        def do_GET(self):
            if not self._valid_host():
                self._send("Host non valido.", 403)
                return
            try:
                update_status()
                config = _read_config(config_path)
                route = urlsplit(self.path)
                if route.path == "/":
                    self._send(_home(config, token, message, process is not None))
                elif route.path in {"/report", "/report/approved", "/review"}:
                    if process is not None:
                        self._send('<p>Raccolta in corso. Il dataset sarà disponibile al termine.</p>'
                                   '<a href="/">Torna al pannello</a>', 409)
                        return
                    dataset = _dataset_path(config)
                    if route.path == "/review":
                        after = parse_qs(route.query).get("after", [""])[0]
                        self._send(_review_page(ReviewSession(dataset), token, after))
                        return
                    if route.path == "/report/approved":
                        dataset = dataset.with_name(f"{dataset.stem}_approved.jsonl")
                    report = render_dashboard(dataset).replace(
                        "<main>", '<main><p><a href="/">Torna al pannello di raccolta</a></p>', 1
                    )
                    self._send(report)
                else:
                    self._send("Pagina non trovata.", 404)
            except FileNotFoundError:
                self._send('<p>Dataset non disponibile. Avvia prima una raccolta.</p><a href="/">Torna al pannello</a>', 404)
            except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
                self._send(f'<p>{escape(str(exc))}</p><a href="/">Torna al pannello</a>', 400)

        def do_POST(self):
            nonlocal message, process, result_file
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not self._valid_host() or not 0 < length <= 16384:
                    raise ValueError
                form = parse_qs(self.rfile.read(length).decode(), max_num_fields=4)
                if not secrets.compare_digest(form.get("token", [""])[0].encode(), token.encode()):
                    raise ValueError
            except ValueError:
                self._send("Richiesta non valida.", 403)
                return
            redirect = "/"
            try:
                update_status()
                if process is not None:
                    self._send('<p>È già in corso una raccolta.</p><a href="/">Torna al pannello</a>', 409)
                    return
                if self.path in {"/seed", "/remove-seed"}:
                    message = _change_seed(config_path, form.get("seed", [""])[0],
                                           remove=self.path == "/remove-seed")
                elif self.path == "/review":
                    session = ReviewSession(_dataset_path(_read_config(config_path)))
                    record_id = form.get("record", [""])[0]
                    decision = form.get("decision", [""])[0]
                    record = next((record for record in session.pending()
                                   if record.get("id") == record_id), None)
                    if record is None or not record_id:
                        self._send('<p>Record non disponibile per la revisione.</p>'
                                   '<a href="/review">Torna alla revisione</a>', 409)
                        return
                    session.decide(record, decision)
                    redirect = "/review"
                elif self.path == "/run":
                    if not _read_config(config_path)["seeds"]:
                        raise ValueError("Aggiungi almeno un seed prima di avviare la raccolta.")
                    result_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
                    try:
                        process = subprocess.Popen(
                            [sys.executable, str(Path(__file__).resolve().parents[1] / "main.py"),
                             "run", "--config", str(config_path), "--report-json"],
                            stdout=result_file,
                        )
                    except OSError:
                        result_file.close()
                        result_file = None
                        raise
                    message = "Raccolta in corso. Lo stato si aggiorna automaticamente."
                else:
                    self._send("Pagina non trovata.", 404)
                    return
            except (OSError, ValueError, yaml.YAMLError) as exc:
                message = str(exc)
            self.send_response(303)
            self.send_header("Location", redirect)
            self.end_headers()

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        address = f"127.0.0.1:{server.server_port}"
        url = f"http://{address}/"
        print(f"Pannello locale: {url}  (Ctrl+C per chiuderlo)", flush=True)
        if open_browser:
            try:
                webbrowser.open(url)
            except webbrowser.Error:
                pass
        try:
            server.serve_forever()
        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                result_file.close()
