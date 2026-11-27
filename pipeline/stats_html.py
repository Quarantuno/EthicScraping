"""Self-contained browser report generated from aggregate dataset statistics."""
import json
from html import escape

from compliance.check_dataset import check_dataset

from .dataset_stats import compute_stats


def _breakdown(title, rows, searchable=False):
    rows = list(rows)
    maximum = max((count for _, count in rows), default=1) or 1
    bars = "".join(
        f'<li><span>{escape(str(label))}</span><strong>{count:,}</strong>'
        f'<progress max="{maximum}" value="{count}" aria-hidden="true"></progress></li>'
        for label, count in rows
    )
    search = ('<label for="domain-search">Cerca un dominio</label>'
              '<input type="search" id="domain-search" aria-controls="domains">'
              if searchable and rows else "")
    empty = '<p>Nessun dato disponibile.</p>' if not rows else ""
    section_id = ' id="domains"' if searchable else ""
    return f'<section><h2>{title}</h2>{search}{empty}<ul{section_id}>{bars}</ul></section>'


_ISSUE_LABELS = {
    "invalid_record": "Record JSON non valido",
    "missing_source_url": "URL di provenienza mancante o non valido",
    "license_evidence_for_review": "Licenza da verificare",
    "tdm_reservation": "Riserva TDM rilevata",
    "missing_tdm_reservation_evidence": "Verifica TDM assente",
    "ai_optout": "Opt-out noai rilevato",
    "missing_ai_optout_evidence": "Verifica noai assente",
}


def _compliance_panel(report):
    counts = report["counts"]
    heading = '<section id="compliance"><h2>Controlli di compliance</h2>'
    summary = (
        f'<p><strong>{report["records_flagged"]:,}</strong> righe da rivedere '
        f'su <strong>{report["records_checked"]:,}</strong> controllate.</p>'
    )
    note = '<p>Verifica le segnalazioni nel file originale prima di usare il dataset.</p>'
    if not counts:
        return heading + summary + "</section>"

    options = "".join(
        f'<option value="{escape(code)}">{escape(_ISSUE_LABELS.get(code, code))} ({count:,})</option>'
        for code, count in sorted(counts.items())
    )
    rows = []
    for finding in report["findings"]:
        issues = finding["issues"]
        labels = ", ".join(_ISSUE_LABELS.get(code, code) for code in issues)
        codes = escape(" ".join(issues))
        rows.append(f'<tr data-issues="{codes}"><th scope="row">{finding["line"]}</th>'
                    f'<td>{escape(labels)}</td></tr>')
    return (heading + summary + note + '<details><summary>Mostra le righe da rivedere</summary>'
            '<div class="finding-controls"><div>'
            '<label for="issue-filter">Tipo di segnalazione</label>'
            '<select id="issue-filter" aria-controls="findings">'
            '<option value="">Tutti i tipi</option>' + options + '</select></div><div>'
            '<label for="line-filter">Numero di riga</label>'
            '<input id="line-filter" type="search" inputmode="numeric" aria-controls="findings">'
            '</div><button type="button" id="export-findings" hidden>Scarica CSV</button></div>'
            '<p role="status" id="filter-status"></p>'
            '<table id="findings"><thead><tr><th scope="col">Riga</th>'
            '<th scope="col">Segnalazioni</th></tr></thead><tbody>'
            + "".join(rows) + '</tbody></table></details></section>')


def render_html(stats, top_n_domains=10, compliance=None):
    domains = stats["domains"]
    quality = stats["quality"]
    summary = (
        ("Record", f"{stats['total_records']:,}"),
        ("Domini", f"{domains['total_domains']:,}"),
        ("Parole", f"{stats['word_counts']['total']:,}"),
        ("Qualità media", f"{quality['mean']:.3f} / 1" if quality["count"] else "—"),
    )
    cards = "".join(f"<div><dt>{label}</dt><dd>{value}</dd></div>"
                    for label, value in summary)
    top = domains["top"][:top_n_domains]
    charts = (
        _breakdown("Domini principali", ((r["domain"], r["records"]) for r in top), True)
        + _breakdown("Lingue", stats["languages"])
        + _breakdown("Licenze", stats["licenses"])
        + _breakdown("Dati personali oscurati", sorted(stats["pii"].items()))
    )
    title = "Dashboard del dataset" if compliance is not None else "Statistiche del dataset"
    navigation = (
        '<nav aria-label="Sezioni del report"><a href="#statistics">Statistiche</a>'
        '<a href="#compliance">Compliance</a></nav>'
        if compliance is not None else ""
    )
    audit = _compliance_panel(compliance) if compliance is not None else ""
    return f"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · EthicScraping</title>
<style>
  :root {{ color-scheme: light; font: 16px/1.45 system-ui, sans-serif;
    color: #162c28; background: #eef4f0; }}
  body {{ margin: 0; }}
  main {{ max-width: 70rem; padding: clamp(1rem, 4vw, 3rem); margin: auto; }}
  header {{ background: #123d33; color: #fff; padding: clamp(1.25rem, 5vw, 3rem);
    border-radius: 1.4rem; }}
  header p {{ margin: 0; color: #bbe0cd; }}
  h1 {{ font-size: clamp(1.8rem, 5vw, 3.25rem); margin: .25rem 0 0; }}
  nav {{ display: flex; flex-wrap: wrap; gap: .75rem; margin-top: 1.25rem; }}
  nav a {{ color: #fff; border: 1px solid #77ac98; padding: .4rem .75rem; border-radius: .5rem; }}
  #compliance {{ margin-top: 1.2rem; }}
  details summary {{ cursor: pointer; font-weight: 650; margin-bottom: .6rem; }}
  details label {{ display: block; }}
  .finding-controls {{ display: grid; gap: .75rem; align-items: end;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 12rem), 1fr)); }}
  button {{ color: #fff; background: #175744; border: 1px solid #175744;
    padding: .45rem .65rem; border-radius: .4rem; font: inherit; cursor: pointer; }}
  button:disabled {{ opacity: .55; cursor: default; }}
  table {{ border-collapse: collapse; table-layout: fixed; width: 100%; }}
  th, td {{ text-align: left; border-bottom: 1px solid #cee0d6; padding: .5rem; overflow-wrap: anywhere; }}
  th:first-child {{ width: 4rem; }}
  [hidden] {{ display: none !important; }}
  dl, .charts {{ display: grid; gap: 1rem; }}
  dl {{ grid-template-columns: repeat(auto-fit, minmax(8rem, 1fr)); margin: 1.2rem 0; }}
  dl div, section {{ background: #fff; border: 1px solid #cee0d6;
    border-radius: 1rem; padding: 1rem 1.25rem; min-width: 0; }}
  dt, label {{ color: #45635a; }}
  dd {{ margin: .2rem 0 0; font-size: 1.55rem; font-weight: 700; }}
  .charts {{ grid-template-columns: repeat(auto-fit, minmax(min(100%, 23rem), 1fr)); }}
  h2 {{ font-size: 1.15rem; margin: 0 0 .75rem; }}
  input, select {{ box-sizing: border-box; width: 100%; font: inherit; color: inherit;
    padding: .45rem .65rem; border: 1px solid #769287; border-radius: .4rem; }}
  :focus-visible {{ outline: 2px solid #237157; outline-offset: 2px; }}
  ul {{ list-style: none; margin: .75rem 0 0; padding: 0; }}
  li {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: .2rem .75rem;
    overflow-wrap: anywhere; margin: .7rem 0; }}
  progress {{ grid-column: 1 / -1; width: 100%; height: .5rem; accent-color: #237157; }}
  footer {{ margin-top: 1.2rem; padding: 0 .25rem; color: #45635a; }}
</style>
</head>
<body>
<main>
  <header><p>EthicScraping</p><h1>{title}</h1>{navigation}</header>
  <dl id="statistics">{cards}</dl>
  <div class="charts">{charts}</div>
  {audit}
  <footer>
    <p>Indice di concentrazione dei domini (HHI): {stats["domain_concentration_index"]}
    · Record con riserva TDM: {stats["tdm"]["records_with_tdm_reservation_flag"]}
    · Record con opt-out noai: {stats["ai_optout"]}</p>
  </footer>
</main>
<script>
  const search = document.getElementById("domain-search");
  if (search) {{
    const rows = [...document.querySelectorAll("#domains li")];
    const status = document.createElement("p");
    status.setAttribute("role", "status");
    search.after(status);
    search.addEventListener("input", () => {{
      const query = search.value.trim().toLowerCase();
      let matches = 0;
      for (const row of rows) {{
        row.hidden = !row.firstElementChild.textContent.toLowerCase().includes(query);
        if (!row.hidden) matches += 1;
      }}
      status.textContent = matches ? "" : "Nessun dominio corrispondente.";
    }});
  }}
  const issueFilter = document.getElementById("issue-filter");
  if (issueFilter) {{
    const findings = [...document.querySelectorAll("#findings tbody tr")];
    const status = document.getElementById("filter-status");
    const lineFilter = document.getElementById("line-filter");
    const exportButton = document.getElementById("export-findings");
    const applyFilters = () => {{
      const code = issueFilter.value;
      const line = lineFilter.value.trim();
      let visible = 0;
      for (const row of findings) {{
        const matchesLine = !line || row.firstElementChild.textContent === line;
        const matchesIssue = !code || row.dataset.issues.split(" ").includes(code);
        row.hidden = !(matchesLine && matchesIssue);
        if (!row.hidden) visible += 1;
      }}
      status.textContent = visible === 1 ? "1 riga mostrata." : visible + " righe mostrate.";
      exportButton.disabled = visible === 0;
    }};
    issueFilter.addEventListener("change", applyFilters);
    lineFilter.addEventListener("input", applyFilters);
    exportButton.hidden = false;
    applyFilters();

    exportButton.addEventListener("click", () => {{
      const records = ["riga,segnalazione"];
      for (const row of findings) {{
        if (row.hidden) continue;
        const line = row.firstElementChild.textContent;
        for (const code of row.dataset.issues.split(" ")) {{
          if (!issueFilter.value || issueFilter.value === code) {{
            records.push(line + "," + code);
          }}
        }}
      }}
      const csv = new Blob([records.join("\\r\\n") + "\\r\\n"], {{type: "text/csv;charset=utf-8"}});
      const url = URL.createObjectURL(csv);
      const link = document.createElement("a");
      link.href = url;
      link.download = "segnalazioni-ethicscraping.csv";
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }});
  }}
</script>
</body>
</html>
"""


def render_dashboard(dataset_path):
    with dataset_path.open(encoding="utf-8") as dataset:
        audit = check_dataset(dataset)
        invalid = {row["line"] for row in audit["findings"] if "invalid_record" in row["issues"]}
        dataset.seek(0)
        records = [json.loads(line) for number, line in enumerate(dataset, 1)
                   if line.strip() and number not in invalid]
    return render_html(compute_stats(records, top_percent=100), top_n_domains=25, compliance=audit)
