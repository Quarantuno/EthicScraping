# EU legal framework: how this pipeline relates to GDPR, the AI Act, and the Copyright Directive

*[Leggi questo in italiano](COMPLIANCE.it.md)*

**This is not legal advice.** It explains, with sources, which EU rules
are relevant to a web-scraping-for-AI-training pipeline and which parts
of this codebase were built with them in mind. Whether your specific use
is compliant depends on facts this code can't know (what you scrape, why,
who you are, where your users are) — talk to a lawyer for that. Laws and
their interpretation also change; the citations below reflect the state
of EU law as understood in September 2026.

## 1. GDPR — when scraped content includes personal data

Web-scraped text very often contains personal data (names, contact
details, opinions attributed to identifiable people) even when nobody
intended to collect it. GDPR applies regardless of whether the data was
"public" — publicly accessible is not the same as "fair game."

- **Article 5** (principles): purpose limitation and **data
  minimization** are why this pipeline redacts PII (`pipeline/pii_filter.py`,
  optionally `pipeline/ner_pii.py`) before writing records, rather than
  keeping raw text "just in case."
- **Article 6** (lawful basis): if you're processing personal data,
  legitimate interest (Art. 6(1)(f)) is the basis most often invoked for
  research/text-mining scraping, but it requires a documented balancing
  test against the individuals' rights — this pipeline doesn't do that
  test for you.
- **Article 9** (special category data): data revealing things like
  health, political or religious views, sexual orientation, etc. gets
  much stricter treatment. **The regex/NER PII filters here do not
  reliably detect special-category data** — they catch structural
  patterns (emails, phone numbers) and common entity types (names,
  places), not sensitive *topics*. High-risk sources (health forums,
  political discussion, etc.) need human review, not just the automated
  filters.
- **[Article 14](https://gdpr-info.eu/art-14-gdpr/)** (information duty
  when data isn't collected from the data subject): this is the article
  most directly about scraping. It requires telling people what data
  about them you collected and why — generally within one month. Article
  14(5)(b) has a "disproportionate effort" exemption often invoked for
  large-scale scraping/research, **but it doesn't mean "do nothing"**:
  the controller must still take "appropriate measures to protect the
  data subject's rights," which explicitly can include making the
  information publicly available. This project's provenance fields
  (`url`, `domain`, `license`, `pii_redactions`, `collected_at` on every
  record) and this document exist partly to make that kind of public
  disclosure possible.
- **Article 17** (right to erasure): this pipeline has **no built-in
  mechanism** to find and remove a specific person's data on request.
  If you publish or train on a dataset containing personal data, you
  need a process for that outside this codebase.

## 2. Text and data mining: Copyright Directive (EU) 2019/790, Articles 3 & 4

Articles 3 and 4 of the [DSM Copyright Directive](https://legalblogs.wolterskluwer.com/copyright-blog/the-new-copyright-directive-text-and-data-mining-articles-3-and-4/)
create an EU-wide exception letting you reproduce and extract lawfully
accessible copyrighted content for text-and-data-mining purposes —
without asking each rightsholder individually. Article 4 (the broader
one, usable for commercial purposes including AI training) has one major
condition: it doesn't apply if the rightsholder has **"expressly
reserved" that right "in an appropriate manner, such as machine-readable
means."**

- `scraper/tdm_rights.py` checks for that machine-readable reservation
  using [TDMRep](https://www.edrlab.org/open-standards/tdmrep/), the
  emerging standard for it (an HTML `<meta name="tdm-reservation">` tag,
  and a `/.well-known/tdmrep.json` file) — deliberately not robots.txt,
  which TDMRep's own authors note wasn't designed for this.
- By default (`output.respect_tdm_optout: true`), `pipeline/dataset_writer.py`
  drops pages with a detected reservation unless they already carry a
  clearly identified permissive license (which grants permission on its
  own terms, independent of the Art. 4 exception).
- **Caveat:** TDMRep adoption is still limited (it's a young standard),
  so the *absence* of a signal is common and doesn't itself prove a
  rightsholder is fine with mining — it just means no machine-readable
  reservation was found. The license detector (`scraper/license_detector.py`)
  and human review (`main.py review`) matter more than this check for
  most sources today.

## 3. AI Act (Regulation (EU) 2024/1689) — if you release a trained model

The AI Act mostly regulates *AI systems and models*, not scraping tools
as such — but it becomes directly relevant the moment you use
`training/` to fine-tune and release a model.

- **Article 53(1)(d)** requires providers of general-purpose AI (GPAI)
  models to publish a **sufficiently detailed public summary of the
  content used for training**, using the European Commission's official
  template (in effect since 2 August 2025; AI Office enforcement began
  2 August 2026). The template's "List of Data Sources" section
  specifically asks for the **top 10% of domains by data volume** for
  web-scraped data (5% for SMEs), among other things.
  `compliance/generate_training_summary.py` generates a **draft** of
  that web-scraped-data subsection directly from your dataset export —
  domain breakdown, license mix, PII-redaction counts, and TDM-opt-out
  handling. It does not fill in the model-level "General Information"
  section (name, version, modalities, compute) since this codebase has
  no way to know that.
- **Article 53(1)(c)** requires GPAI providers to have a **policy to
  comply with Union copyright law**, explicitly including respecting
  Article 4(3) TDM reservations. The `respect_tdm_optout` behavior
  described above is a concrete piece of implementing such a policy —
  not the whole of it (a real policy needs to be written down and cover
  more than one script's behavior).
- Non-compliance for GPAI obligations can carry fines up to **€15
  million or 3% of global annual turnover**.
- This project doesn't attempt to address the AI Act's separate
  high-risk-system obligations (Title III), which depend entirely on
  what the resulting model is *used for*, not on how its data was
  collected.

## What this means practically

1. Run `main.py review` before training on anything (GDPR data
   minimization + a human check that automated filters didn't miss
   special-category data or content someone asked to be excluded from).
2. Leave `respect_tdm_optout: true` unless you have a specific,
   documented reason not to.
3. If you release a fine-tuned model, run
   `compliance/generate_training_summary.py` as a starting draft for
   your Article 53(1)(d) summary, and get it reviewed — this tool
   doesn't know about your model, your legal entity, or your users.
4. If your dataset will contain personal data at any real scale, talk to
   a lawyer about Article 14 disclosure and your Article 17 process
   *before* you publish, not after.

## Sources

- [Art. 14 GDPR – gdpr-info.eu](https://gdpr-info.eu/art-14-gdpr/)
- [The New Copyright Directive: Text and Data Mining (Articles 3 and 4) — Kluwer Copyright Blog](https://legalblogs.wolterskluwer.com/copyright-blog/the-new-copyright-directive-text-and-data-mining-articles-3-and-4/)
- [TDM Reservation Protocol (TDMRep) — EDRLab](https://www.edrlab.org/open-standards/tdmrep/)
- [European Commission Releases Mandatory Template for Public Disclosure of AI Training Data — WilmerHale](https://www.wilmerhale.com/en/insights/blogs/wilmerhale-privacy-and-cybersecurity-law/european-commission-releases-mandatory-template-for-public-disclosure-of-ai-training-data)
- [EU Publishes Template for Public Summaries of AI Training Content — Securiti](https://securiti.ai/eu-publishes-template-for-public-summaries-of-ai-training-content/)
- [Template for the public summary of training content for GPAI models — Regulations.AI](https://regulations.ai/regulations/european-union-2025-7-template-training-summary)
