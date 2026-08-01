# Automated Literature Review — user manual

A desktop tool that takes a research question from *"what should I read?"* to
*"here is a spreadsheet of what every paper said, and how much of it I can
trust"*, without you opening the PDFs one at a time.

- **New here?** Read *What the tool does*, then *Concepts*, then work through
  the *Worked example*.
- **Looking for a specific button?** Jump to the tab you are on.
- **Looking for a function or class?** See [CODE_REFERENCE.md](CODE_REFERENCE.md),
  or open [CODE_MAP.html](CODE_MAP.html) in a browser to see how it links to
  everything else.
- **Looking for exact behaviour and edge cases?** See
  [FUNCTIONALITY_AUDIT.md](FUNCTIONALITY_AUDIT.md).

---

## 1. What the tool does

Four stages, each usable on its own:

| Stage | You give it | You get back |
| --- | --- | --- |
| **Collect** | A research area + question | Ranked search phrases, and a publication list from OpenAlex / Google Scholar, optionally tagged by your keywords |
| **Analyze** | A PDF, or a folder of PDFs | Per document: identified sections, 7 abstract attributes, 4 introduction attributes, 5 results-&-conclusion attributes, references, DOI/metadata, topic classification |
| **Build & Query** | An analyzed folder | Text + FAISS vector databases per attribute, and a query report answering a question across every paper |
| **Review** | Everything above | One SQL database over all your folders, filterable tables, grouped overviews, charts and Excel exports |

Plus an **Evaluation** stage that scores how well each extracted attribute is
actually supported by the paper's own text — grounding, lexical overlap,
edit distance and embedding cosine.

### What it extracts per document

- **Abstract** — Research Problem · Objective · Methodology · Conclusion ·
  Results · Research Areas · Key Concepts
- **Introduction** — Background · Motivation · Gaps & Limitations · RQs & Scope
- **Results & Conclusion** — Results Mentioned · Limitations or Boundary
  Conditions · Summary of the Content · Future Work · Outlook
- **Also** — the reference list as structured citations, tables and figures as
  files, DOI/publisher/year/authors, and free-text topic classification.

---

## 2. Concepts

Five ideas explain nearly every behaviour in the UI.

**Storage space.** A folder that holds everything about one batch of papers —
analysis JSONs, logs, databases, reports. You pick it once; the tool builds the
whole tree inside it. You can have as many as you like (one per topic, per
review, per project).

**Active storage space.** The field at the top of the window. Analyze, Query
and Evaluation all default to it, so you choose a folder once instead of
re-browsing on every tab. Each of those tabs has a *"Use active space"* tick you
can clear to override it just there.

**The review database.** A single SQLite file,
`~/Automated Literature Review/alr_analyzed_data.db`, that consolidates *every*
storage space. It is what the Review tool reads. Analysis writes into it as it
goes; you can also link a space to it by hand at any time.

**Dated files are history, not clutter.** Outputs are named
`{date}_Abstract_Eval_Overview.xlsx`, `{date}_Title_Classification.xlsx` and so
on. Re-running writes *today's* file and never edits an older one, so you can
always see what a previous run concluded. Where a pass offers *reuse existing*,
it pulls the newest previous file forward.

**Nothing is paid for twice.** Before any expensive step the tool checks
whether the answer already exists — in the space, or in the database — and
copies it instead of re-asking the model. This is why a re-run over 800 PDFs
can finish in minutes. If you *want* a fresh answer, choose *Run fresh* when
asked, or use the Enrichment tab's re-run buttons.

---

## 3. Getting started

### Launching

**From a packaged build** (no Python needed) — unzip and run:

- `AutomatedLiteratureReview.exe` — the main tool
- `ReviewTool.exe` — the Review tool on its own

Keep the whole folder together; neither runs if you copy just the `.exe` out.

**From source:**

```bash
pip install -r requirements.txt
pip install -e .
python src/gui_main.py        # main tool
python src/review_main.py     # Review tool
```

### First launch

The tool creates `~/Automated Literature Review/` for its configuration and the
review database. Nothing else is written until you point it at a storage space.

### Choosing a model

The strip along the top shows the **active LLM provider, its model, and whether
a key is stored**. Each tab that calls a model has a *Choose Model…* button.
Set the key once; it is remembered.

Embedding models are chosen separately (*Choose Embedding Model…*) because
embeddings can run either through an API or locally.

> **Keep one embedding model per space.** A vector database built with one
> model cannot be compared against vectors from another. The tool detects a
> mismatch and refuses to reuse the index rather than returning meaningless
> similarity scores — but you will pay to re-embed.

### The window

- **Top:** provider/model/key status.
- **Under it:** the active storage space.
- **Middle:** the tabs.
- **Bottom:** the **console drop-down**. `▾ Hide console log` folds it away and
  gives the space to the tabs; `▸ Show console log` brings it back. The bar
  itself always stays visible, showing the last action's result (green = fine,
  red = failed or cancelled), and grows a **●** when output arrives while the
  log is folded. **Clear log** empties it. **Verbose log** shows the raw
  developer stream instead of the filtered activity feed.

Tabs 4 and 5 are tucked behind the **`+`** at the end of the tab strip — click
it to open Evaluation or Enrichment.

Long passes run in the background with a progress bar and a **Cancel** button.
Cancelling stops at the next safe point and keeps everything already finished.

---

## 4. Tab 1 — Collect Literature

*Goal: from a research question to a list of papers worth reading.*

1. **Data Storage Configuration** — pick where this search's files go.
2. **Research Scope & Details** — enter your **Research Area** and **Research
   Question**. *Generate Scope via LLM* drafts a scope statement from them;
   tick *Use scope in next steps* to feed it into keyword and phrase generation.
3. **Keywords** — *Suggest Keywords via LLM*, type your own with **Add**, or
   **Import from File…** to reload a previous run's list. Tick the ones to use
   (there is a select-all).
4. **Process Selected Keywords → Search Phrases** — builds candidate phrases and
   ranks them four ways: by Research Area, by Research Question, by both
   combined, and by total rank. Switch ranking with the radio buttons; tick the
   phrases you want.
5. **Run Publication Search** — queries **OpenAlex** or **Google Scholar**.
   Scholar blocks aggressive clients, so the tool rate-limits itself and backs
   off after a block; OpenAlex is the more reliable default. A count appears
   when results land.
6. **Classify Publications by Keywords** — tags the result list against your
   keywords so you can see which papers match which concern.
   *Save Ranking to Excel Only* writes the ranked list without classifying.

**Output:** dated keyword, phrase, publication and classification workbooks in
the collection folder.

---

## 5. Tab 2 — Analyze Literature

*Goal: turn PDFs into structured data. This is the core of the tool.*

1. **Input Selection Targets** — *Select File* for one PDF, or *Select Folder*
   for a batch (it recurses into subfolders).
2. **Analysis Storage Config** — where the results go. Tick *Use Custom Storage
   Folder Path Location?* to override the active space. **This folder becomes
   the active space for the other tabs.**
3. **Components to Extract** — tick what you want:
   - *Sections (incl. tables/images)* — **required**; everything else reads its output
   - *Abstract*, *Introduction*, *Results & Conclusion*
   - *References*
   - *DOI / metadata*
   - *Classification* — separately *by Title* and *by Abstract*
   - *Build Text DB* and *Build Vector DB* — the RAG databases (see Tab 3)
4. **Skip duplicate titles** — compares each PDF's real title against what you
   have already analyzed and skips genuine duplicates. The scan prints a
   breakdown: how many will be processed, and per reason how many were skipped.
5. **Execute Document Extraction & Analysis.**

Each PDF goes through every selected step before the next one starts, so
progress is real progress, and a crash keeps what finished.

**If one PDF fails, the run continues.** The failure is recorded in
`Analysis_not_added.xlsx` in the space with its stage and error. A PDF that
hangs Docling is killed after 15 minutes, logged, and the run moves on.

### At the end: the completeness dialog

When the batch finishes, the tool inspects the database and reports what is
*missing* per stage — classification, evaluation, DOI, introduction/references
— and how much of each a **previous file already on disk** could fill. You then
choose per stage:

- **Reuse existing** — copy it across, no model calls, no cost
- **Run fresh** — re-run the stage
- **Skip** — leave it

Only offered where something is genuinely reusable. If the database is already
complete, no dialog appears. Unanswered after 15 minutes it answers itself with
the safe choice (reuse where free, skip anything that would cost).

### Resolve Missing Attributes (no PDFs needed)

The second button on this tab finishes an **already-analyzed space with no
input file or folder at all**. Everything it needs is on disk, so nothing is
re-extracted and Docling is never loaded.

It first runs a **free scan** — one file read per document, no model calls — and
tells you how many documents and empty attributes are eligible *before* you
commit. Documents whose gaps have already used both completion attempts are
reported separately rather than retried pointlessly. It then fills what it can,
writes the values back, and pushes each document into the review database as
soon as it is filled, so cancelling keeps everything resolved so far.

Use this when a batch finished with gaps and you do not want to re-run PDFs.

---

## 6. Tab 3 — Query Execution

*Goal: ask one question across every paper you have analyzed.*

Requires the **text + vector databases** (tick them on Tab 2, or use
*Rebuild section databases…* here).

1. Point at the storage space (or tick *Use active space*).
2. Choose **which attribute types to search** — abstract, introduction and
   results-&-conclusion attributes are all available.
3. Choose **which attributes become columns** in the report.
4. Set **top-k** — how many best matches to return.
5. Optionally tick *Harvest matched files* to copy the matched documents' JSONs
   next to the report.
6. **Generate Query Report.**

**Output:** a query workbook with one row per matched document, its similarity,
and a column per attribute you selected.

### Common Database

The frame below combines **several storage spaces into one queryable
database**, so a query can span every review you have run. *Add space…* picks
folders one at a time; *Scan folder for spaces…* finds them under a parent
folder. Documents already present are skipped, so updating is incremental. You
can also treat matching filenames as duplicates, not just matching UUID/title.

---

## 7. Tab 4 — Evaluation

*Goal: measure whether the extracted data is actually in the paper.*

Pick any combination of:

- **Substring match (data grounding)** — the core check. `Research Areas` and
  `Key Concepts` are matched as whole strings, since they are short terms
  lifted verbatim. Every other attribute is **graded word by word**: articles
  and prepositions are dropped, and each remaining content word is looked up in
  the paper's own identified text. You get `Words_Checked`, `Words_Found`,
  `Grounding_%` and the words that were missing.
  An attribute the analyzer never produced counts **neither for nor against** —
  it is recorded but does not vote, so an extraction gap cannot masquerade as a
  grounding failure.
- **Lexical overlap** — Jaccard, ROUGE-1/2/L, BLEU
- **Distance & structural alignment** — Levenshtein, similarity ratio, WER
- **Cosine similarity** — embeddings; reuses the space's FAISS indexes where
  they exist and builds them where they do not

…against any of **Abstract**, **Introduction**, **Results & Conclusion**.

Metrics are **sentence-level**: the reference text is split into sentences,
every extracted item is scored against *each* sentence, and the workbook keeps
the best value plus the sentence that produced it. The complete
item × sentence matrix is stored as JSON per document.

**Output:** per-attribute evaluation workbooks, one dated workbook per metric
kind, a combined overview, and a **per-metric workbook** with one sheet per
individual metric (`grounding`, `jaccard`, `rouge1`, … `cosine_similarity`) —
one row per extracted item across every section and every document, so a single
metric can be sorted and charted across the whole space. Scores also go to the
review database.

Below the batch runner, **Manual text comparison** scores two pasted texts
directly, and **Custom Topic Classification** lets you define your own topic and
tags; results become a dated workbook and a new database column named after the
topic.

---

## 8. Tab 5 — Enrichment

*Goal: run one pass over an already-analyzed space, without redoing the rest.*

- **Re-run Abstract Analysis** / **Re-run Reference Extraction**
- **Classify Titles** / **Classify Abstracts**
- **Build Master Excel DB** — one workbook, one sheet per attribute, every
  document. You pick which attributes to include.
- **Enrich from Download Logs** — matches your download/bibliography workbooks
  to analyzed documents and fills in publisher, year, authors, links
- **DOI / Metadata Extraction** — standalone, for a file or a folder
- **Classify Title** — a single title, typed in
- **Question-Scored Classification** — asks a fixed question set per document
  and scores the answers

All of these use the shared storage space and LLM service at the top of the tab
(the same fields as the Evaluation tab — they stay in sync).

---

## 9. The Review tool

Launch it from the main app or run `ReviewTool.exe` on its own. Two groups:

### Import & Enrich

- **Storage Spaces** — lists recognised spaces. Select one and *Link to
  database* (or *Link ALL*), *Extract DOI/metadata*, *Classify (title +
  abstract)*, *Evaluate data*, or *Open folder*. Also imports bibliographic
  workbooks (`*_download_log`, `*_DOI_Metadata`, `publications_metadata`).
- **Data Files** — lists the workbooks in the loaded spaces, lets you tick
  several and **merge them into one table** (one row per document, newest run
  kept), export a group, or check and update what SQL holds.
- **Section Editor** — review and correct a document's identified section text
  when the automatic detection got it wrong.

### Review & Explore

- **Documents** — browse analyzed documents.
- **Document Inspector** — find one document by title/filename/UUID. It reads
  the database first and fills anything missing from the storage space, shows
  every field with the full value, and can **open the PDF** or locate it.
- **Database** — statistics, the full document table with filters, column
  export to Excel, and a **read-only SQL box** for your own `SELECT`s.
- **Overviews** — the reporting surface. Choose columns, add filters, group,
  preview, chart, and export to Excel/CSV. Overviews can be **saved as named
  templates**, and *Build from description* drafts one from a sentence.
- **Help** — what each column means and how the tabs fit together, in the app.

The main window has per-tab help too: each tab explains its own workflow
without leaving the app.

---

## 10. Worked example

**Goal: "What methods are used for fault detection in avionics, and what do
authors say the open gaps are?"**

1. **Tab 1** — Research Area *"fault detection in avionics systems"*, Research
   Question as above. Generate scope → suggest keywords → tick the good ones →
   generate phrases → pick the top-ranked → run the search on OpenAlex.
2. Download the PDFs you want into one folder.
3. **Tab 2** — *Select Folder*, choose a storage space, tick Sections,
   Abstract, Introduction, Results & Conclusion, References, DOI, both
   Classification boxes, and both RAG database boxes. Leave *Skip duplicate
   titles* on. Run it. At the end, take **Reuse existing** wherever offered.
4. **Tab 4** — tick *Substring match* and *Cosine similarity*, all three
   targets, Run Evaluation. Check `Grounding_%` — low values mean the analyzer
   paraphrased beyond the source, and are worth a look in the Section Editor.
5. **Tab 3** — query *"methods for fault detection"* with Methodology and
   Results Mentioned as columns, top-k 20. Then query *"open research gaps"*
   with Gaps & Limitations and Future Work as columns.
6. **Review tool → Overviews** — columns Title, Year, Methodology,
   Gaps & Limitations, `evaluation_score`; filter to your classification;
   group by year; export to Excel.

---

## 11. Where everything is stored

```
~/Automated Literature Review/
├── alr_analyzed_data.db          the review database (every space)
├── API_keys_config.json          your stored provider keys
├── ui_session_state.json         last-used paths and settings
├── model_lists_cache.json        cached model lists (refreshed on request)
├── 00_Crash_Logs/                timestamped tracebacks
├── 00_LLM_Log_Data/              a record of the model calls made
├── 01_Collection/                default home for collection runs
├── 02_Analyzed_Data/             default home for storage spaces
├── 10_Vector_DBs/                default home for RAG databases
└── 20_Overviews/                 exported overviews and charts
```

These numbered folders are the **defaults**. Whenever you point a tab at a
custom folder, that folder is used instead — which is how one space per project
works.

Inside a storage space:

```
<your space>/
├── Processed_file_registry.xlsx  one row per analyzed document
├── Analyzed_Data_Files/          per-document analysis JSONs
│   ├── Abstract_Data_Files/
│   ├── Introduction_Data_Files/
│   └── ...
├── Raw_Section_JSON_Files/       identified sections + their chunks
├── Raw_Chunk_files/              Docling output cache
├── Raw_Tables_files/  Raw_Images_files/
├── References_JSON_Files/
├── Abstract_DB/  Introduction_DB/  Results_Conclusion_DB/
│   ├── *_DB.xlsx / *_DB.json      text databases per attribute
│   ├── *_DB.bin                   FAISS vector indexes
│   └── *_Eval*.xlsx               evaluation workbooks
├── DOI_Metadata_Files/  Publication_Classification_Files/
├── Querry_results/  Attribute_Query_Results/
└── *_not_added.xlsx              anything a pass skipped, with the reason
```

Skip logs (`Analysis_not_added.xlsx`, `Evaluation_not_added.xlsx`,
`Common_DB_not_added.xlsx`, …) are **append-only**: nothing a pass could not
process is ever silently dropped.

---

## 12. Troubleshooting

**"database is locked" during a pass.** Fixed in current builds (the database
runs in WAL mode with a 30-second wait). On an older build, close the Review
tool while a long pass runs. If you saw this warning, the *workbooks* are
complete but the database summary for those documents was lost — re-run the
pass in reuse/copy mode to fill it in without recomputing.

**The progress bar freezes for a while.** Long passes checkpoint their
workbooks to disk, and nothing reports progress from inside a checkpoint.
Current builds space checkpoints in time and announce them
(`💾 Checkpointing …`). If the bar is frozen with *no* console output for many
minutes during a **cosine** evaluation, that is a network call to the embedding
API, not a hang.

**"Invalid value 'False' for dtype 'float64'".** An older-build write failure on
evaluation sheets; fixed. Update and re-run the evaluation.

**Only a few PDFs are processed out of many.** The duplicate scan skipped the
rest. Read its breakdown in the console — it says per reason how many and why,
and a stale "duplicate" verdict whose original is no longer analyzed is
automatically re-queued.

**Attributes come back "No information available".** The section was found but
the model could not fill that field. Use **Resolve Missing Attributes** on
Tab 2 — it retries with a wider window, costs nothing for documents that are
already complete, and never spends the same attempt twice.

**OCR.** Docling OCR uses RapidOCR on ONNX Runtime. It fetches its models on
first use, so the first OCR run needs internet.

**Local models are not bundled.** Embedding and local HF models load from the
paths in `alr/common/LLM_Config.py`. Without them the app still starts and all
API-based features work.

---

## 13. Building the executables

Two applications are built from one recipe (`UI_pipeline.spec`) into one
shared folder: `AutomatedLiteratureReview.exe` and `ReviewTool.exe`.

```bat
build_exe.bat
```

**A `.exe` must be built on Windows** — PyInstaller does not cross-compile.
Without a Windows machine, use the repository's GitHub Actions job
(**Actions → Build Windows EXE → Run workflow**), which builds on a Windows
runner and uploads both executables as a downloadable artifact.

On macOS/Linux, `build_app.sh` produces native binaries — useful for validating
the recipe, not for distribution to Windows users.

See [BUILD_EXE.md](BUILD_EXE.md) for the details (bundle size, OCR models, the
console-window setting).
