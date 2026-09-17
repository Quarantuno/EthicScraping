# Quadro normativo UE: come questa pipeline si rapporta a GDPR, AI Act e Direttiva Copyright

*[Read this in English](COMPLIANCE.md)*

**Questo non e' consulenza legale.** Spiega, con fonti, quali norme UE
sono rilevanti per una pipeline di scraping per l'addestramento di AI e
quali parti di questo codice sono state costruite tenendole a mente. Se
il tuo uso specifico e' conforme dipende da fatti che questo codice non
puo' conoscere (cosa scrappi, perche', chi sei, dove sono i tuoi utenti)
— per quello serve un avvocato. Anche le leggi e la loro interpretazione
cambiano nel tempo; le citazioni qui sotto riflettono lo stato del
diritto UE cosi' come compreso a settembre 2026.

## 1. GDPR — quando i contenuti scrappati includono dati personali

Il testo raccolto dal web contiene molto spesso dati personali (nomi,
contatti, opinioni attribuite a persone identificabili) anche quando
nessuno intendeva raccoglierli. Il GDPR si applica indipendentemente dal
fatto che il dato fosse "pubblico" — accessibile pubblicamente non
equivale a "libero da vincoli."

- **Articolo 5** (principi): la limitazione delle finalita' e la
  **minimizzazione dei dati** sono il motivo per cui questa pipeline
  redige le PII (`pipeline/pii_filter.py`, opzionalmente
  `pipeline/ner_pii.py`) prima di scrivere i record, invece di tenere il
  testo grezzo "per sicurezza."
- **Articolo 6** (base giuridica): se stai trattando dati personali, il
  legittimo interesse (art. 6(1)(f)) e' la base piu' spesso invocata per
  lo scraping a fini di ricerca/text-mining, ma richiede un test di
  bilanciamento documentato rispetto ai diritti degli interessati —
  questa pipeline non fa quel test al posto tuo.
- **Articolo 9** (categorie particolari di dati): i dati che rivelano
  cose come salute, opinioni politiche o religiose, orientamento
  sessuale ecc. hanno un trattamento molto piu' rigoroso. **I filtri
  PII regex/NER qui presenti non rilevano in modo affidabile le
  categorie particolari** — catturano pattern strutturali (email,
  telefoni) e tipi di entita' comuni (nomi, luoghi), non *argomenti*
  sensibili. Le fonti ad alto rischio (forum sulla salute, discussioni
  politiche, ecc.) richiedono revisione umana, non solo i filtri
  automatici.
- **[Articolo 14](https://gdpr-info.eu/art-14-gdpr/)** (obbligo di
  informazione quando i dati non sono raccolti dall'interessato): e'
  l'articolo piu' direttamente legato allo scraping. Richiede di
  informare le persone su quali dati che le riguardano hai raccolto e
  perche' — di norma entro un mese. L'art. 14(5)(b) prevede un'esenzione
  per "sforzo sproporzionato" spesso invocata per scraping/ricerca su
  larga scala, **ma non significa "non fare nulla"**: il titolare deve
  comunque adottare "misure appropriate per tutelare i diritti
  dell'interessato," che possono esplicitamente includere rendere
  l'informazione disponibile al pubblico. I campi di provenienza di
  questo progetto (`url`, `domain`, `license`, `pii_redactions`,
  `collected_at` su ogni record) e questo stesso documento esistono in
  parte per rendere possibile quel tipo di divulgazione pubblica.
- **Articolo 17** (diritto alla cancellazione): questa pipeline **non ha
  alcun meccanismo integrato** per trovare e rimuovere su richiesta i
  dati di una persona specifica. Se pubblichi o addestri su un dataset
  che contiene dati personali, serve un processo per questo al di fuori
  di questo codice.

## 2. Text e data mining: Direttiva Copyright (UE) 2019/790, artt. 3 e 4

Gli articoli 3 e 4 della [Direttiva Copyright DSM](https://legalblogs.wolterskluwer.com/copyright-blog/the-new-copyright-directive-text-and-data-mining-articles-3-and-4/)
creano un'eccezione a livello UE che permette di riprodurre ed estrarre
contenuti protetti da copyright ma lecitamente accessibili per finalita'
di text-and-data-mining — senza chiedere il permesso a ogni titolare dei
diritti individualmente. L'articolo 4 (quello piu' ampio, utilizzabile
anche per scopi commerciali incluso l'addestramento di AI) ha una
condizione importante: non si applica se il titolare dei diritti ha
**"espressamente riservato" tale diritto "in modo appropriato, ad
esempio mediante strumenti leggibili da dispositivo automatico."**

- `scraper/tdm_rights.py` verifica questa riserva leggibile da macchina
  usando [TDMRep](https://www.edrlab.org/open-standards/tdmrep/), lo
  standard emergente per questo (un meta tag HTML
  `<meta name="tdm-reservation">` e un file
  `/.well-known/tdmrep.json`) — deliberatamente non robots.txt, che gli
  stessi autori di TDMRep segnalano non essere stato pensato per questo.
- Di default (`output.respect_tdm_optout: true`),
  `pipeline/dataset_writer.py` scarta le pagine con una riserva
  rilevata, a meno che non portino gia' una licenza permissiva
  chiaramente identificata (che concede il permesso secondo i propri
  termini, indipendentemente dall'eccezione dell'art. 4).
- **Attenzione:** l'adozione di TDMRep e' ancora limitata (e' uno
  standard giovane), quindi l'*assenza* di un segnale e' comune e non
  dimostra di per se' che un titolare dei diritti sia d'accordo con il
  mining — significa solo che non e' stata trovata nessuna riserva
  leggibile da macchina. Il rilevamento della licenza
  (`scraper/license_detector.py`) e la revisione umana (`main.py
  review`) contano oggi piu' di questo controllo per la maggior parte
  delle fonti.

## 3. AI Act (Regolamento (UE) 2024/1689) — se pubblichi un modello addestrato

L'AI Act regola principalmente *sistemi e modelli di AI*, non gli
strumenti di scraping in quanto tali — ma diventa direttamente rilevante
nel momento in cui usi `training/` per fare fine-tuning e pubblicare un
modello.

- **L'articolo 53(1)(d)** richiede ai fornitori di modelli di AI per
  finalita' generali (GPAI) di pubblicare un **riepilogo pubblico
  sufficientemente dettagliato dei contenuti usati per l'addestramento**,
  usando il modello ufficiale della Commissione Europea (in vigore dal 2
  agosto 2025; l'applicazione da parte dell'AI Office e' iniziata il 2
  agosto 2026). La sezione "Elenco delle fonti di dati" del modello
  chiede specificamente il **10% principale di domini per volume di
  dati** per i dati scrappati dal web (5% per le PMI), tra le altre
  cose. `compliance/generate_training_summary.py` genera una **bozza**
  di quella sottosezione sui dati scrappati direttamente dal tuo export
  del dataset — ripartizione per dominio, mix di licenze, conteggi delle
  redazioni PII e gestione degli opt-out TDM. Non compila la sezione
  "Informazioni generali" a livello di modello (nome, versione,
  modalita', calcolo) perche' questo codice non ha modo di saperlo.
- **L'articolo 53(1)(c)** richiede ai fornitori GPAI di avere una
  **politica di conformita' al diritto d'autore dell'Unione**, che
  include esplicitamente il rispetto delle riserve TDM dell'art. 4(3).
  Il comportamento `respect_tdm_optout` descritto sopra e' un pezzo
  concreto dell'implementazione di tale politica — non tutta la
  politica (una politica vera va messa per iscritto e coprire piu' del
  comportamento di un singolo script).
- La non conformita' agli obblighi GPAI puo' comportare multe fino a
  **15 milioni di euro o il 3% del fatturato annuo globale**.
- Questo progetto non affronta gli obblighi separati dell'AI Act per i
  sistemi ad alto rischio (Titolo III), che dipendono interamente da
  *per cosa* viene usato il modello risultante, non da come sono stati
  raccolti i suoi dati.

## Cosa significa in pratica

1. Lancia `main.py review` prima di addestrare su qualsiasi cosa
   (minimizzazione dei dati GDPR + un controllo umano che i filtri
   automatici non abbiano perso categorie particolari di dati o
   contenuti che qualcuno ha chiesto di escludere).
2. Lascia `respect_tdm_optout: true` a meno che tu non abbia una ragione
   specifica e documentata per non farlo.
3. Se pubblichi un modello fine-tuned, lancia
   `compliance/generate_training_summary.py` come bozza di partenza per
   il tuo riepilogo art. 53(1)(d), e falla rivedere — questo strumento
   non sa nulla del tuo modello, della tua entita' legale o dei tuoi
   utenti.
4. Se il tuo dataset conterra' dati personali su una scala reale, parla
   con un avvocato della divulgazione ex art. 14 e del tuo processo ex
   art. 17 *prima* di pubblicare, non dopo.

## Fonti

- [Art. 14 GDPR – gdpr-info.eu](https://gdpr-info.eu/art-14-gdpr/)
- [The New Copyright Directive: Text and Data Mining (Articles 3 and 4) — Kluwer Copyright Blog](https://legalblogs.wolterskluwer.com/copyright-blog/the-new-copyright-directive-text-and-data-mining-articles-3-and-4/)
- [TDM Reservation Protocol (TDMRep) — EDRLab](https://www.edrlab.org/open-standards/tdmrep/)
- [European Commission Releases Mandatory Template for Public Disclosure of AI Training Data — WilmerHale](https://www.wilmerhale.com/en/insights/blogs/wilmerhale-privacy-and-cybersecurity-law/european-commission-releases-mandatory-template-for-public-disclosure-of-ai-training-data)
- [EU Publishes Template for Public Summaries of AI Training Content — Securiti](https://securiti.ai/eu-publishes-template-for-public-summaries-of-ai-training-content/)
- [Template for the public summary of training content for GPAI models — Regulations.AI](https://regulations.ai/regulations/european-union-2025-7-template-training-summary)
