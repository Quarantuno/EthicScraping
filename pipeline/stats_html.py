"""Self-contained browser report generated from aggregate dataset statistics."""
from html import escape


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


def render_html(stats, top_n_domains=10):
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
    return f"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Statistiche del dataset · EthicScraping</title>
<style>
  :root {{ color-scheme: light; font: 16px/1.45 system-ui, sans-serif;
    color: #162c28; background: #eef4f0; }}
  body {{ margin: 0; }}
  main {{ max-width: 70rem; padding: clamp(1rem, 4vw, 3rem); margin: auto; }}
  header {{ background: #123d33; color: #fff; padding: clamp(1.25rem, 5vw, 3rem);
    border-radius: 1.4rem; }}
  header p {{ margin: 0; color: #bbe0cd; }}
  h1 {{ font-size: clamp(1.8rem, 5vw, 3.25rem); margin: .25rem 0 0; }}
  dl, .charts {{ display: grid; gap: 1rem; }}
  dl {{ grid-template-columns: repeat(auto-fit, minmax(8rem, 1fr)); margin: 1.2rem 0; }}
  dl div, section {{ background: #fff; border: 1px solid #cee0d6;
    border-radius: 1rem; padding: 1rem 1.25rem; min-width: 0; }}
  dt, label {{ color: #45635a; }}
  dd {{ margin: .2rem 0 0; font-size: 1.55rem; font-weight: 700; }}
  .charts {{ grid-template-columns: repeat(auto-fit, minmax(min(100%, 23rem), 1fr)); }}
  h2 {{ font-size: 1.15rem; margin: 0 0 .75rem; }}
  input {{ box-sizing: border-box; width: 100%; font: inherit; color: inherit;
    padding: .45rem .65rem; border: 1px solid #769287; border-radius: .4rem; }}
  input:focus-visible {{ outline: 2px solid #237157; outline-offset: 2px; }}
  ul {{ list-style: none; margin: .75rem 0 0; padding: 0; }}
  li {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: .2rem .75rem;
    overflow-wrap: anywhere; margin: .7rem 0; }}
  li[hidden] {{ display: none; }}
  progress {{ grid-column: 1 / -1; width: 100%; height: .5rem; accent-color: #237157; }}
  footer {{ margin-top: 1.2rem; padding: 0 .25rem; color: #45635a; }}
</style>
</head>
<body>
<main>
  <header><p>EthicScraping</p><h1>Statistiche del dataset</h1></header>
  <dl>{cards}</dl>
  <div class="charts">{charts}</div>
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
</script>
</body>
</html>
"""
