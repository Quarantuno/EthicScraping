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
from urllib.parse import parse_qs, urlsplit

import yaml

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


def _add_seed(path, url):
    config = _read_config(path)
    if url in config["seeds"]:
        return "Il seed è già presente."
    config["seeds"].append(url)
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
    return "Seed aggiunto alla configurazione."


def _home(config, token, message):
    seeds = "".join(f"<li>{escape(seed)}</li>" for seed in config["seeds"])
    return f"""<!doctype html><html lang="it"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pannello di raccolta · EthicScraping</title>
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
  li {{ overflow-wrap:anywhere }}
  pre {{ color:#193d33;white-space:pre-wrap;overflow-wrap:anywhere }}
</style>
<main><header><p>EthicScraping</p><h1>Pannello di raccolta</h1></header>
<section><h2>Fonti</h2><form method="post" action="/seed">
<input type="hidden" name="token" value="{token}">
<label for="seed">Dominio o URL iniziale</label>
<input id="seed" name="seed" required maxlength="4096" placeholder="https://example.org/articoli">
<button>Aggiungi seed</button></form><ul>{seeds}</ul></section>
<section><h2>Raccolta e controlli</h2><div class="actions">
<form method="post" action="/run"><input type="hidden" name="token" value="{token}">
<button>Avvia raccolta</button></form>
<a class="button" href="/report">Apri statistiche e controlli</a></div>
<p>Il report verifica il dataset salvato. La raccolta può richiedere alcuni minuti.</p>
<pre role="status">{escape(message)}</pre></section></main></html>"""


def serve(config_path, open_browser):
    config_path = config_path.resolve()
    _read_config(config_path)
    token = secrets.token_urlsafe(32)
    message = ""

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
                config = _read_config(config_path)
                if self.path == "/":
                    self._send(_home(config, token, message))
                elif self.path == "/report":
                    dataset = Path((config.get("output") or {}).get("dataset_path", "dataset/output.jsonl"))
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
            nonlocal message
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
            try:
                if self.path == "/seed":
                    message = _add_seed(config_path, _seed_url(form.get("seed", [""])[0]))
                elif self.path == "/run":
                    result = subprocess.run(
                        [sys.executable, str(Path(__file__).resolve().parents[1] / "main.py"),
                         "run", "--config", str(config_path), "--report-json"],
                        stdout=subprocess.PIPE, text=True, check=False,
                    )
                    report = json.loads(result.stdout) if result.returncode == 0 else None
                    message = (
                        f'Raccolta terminata. Nuovi record: {report["pipeline"]["written"]}.\n'
                        f'Esiti richieste: {json.dumps(report["fetch_statuses"], ensure_ascii=False)}'
                        if report else "Raccolta interrotta. Controlla il terminale per i dettagli."
                    )
                else:
                    self._send("Pagina non trovata.", 404)
                    return
            except (OSError, ValueError, yaml.YAMLError) as exc:
                message = str(exc)
            self.send_response(303)
            self.send_header("Location", "/")
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
        server.serve_forever()
