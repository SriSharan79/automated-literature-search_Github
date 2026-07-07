---
name: alr-development
description: Working method for the alr package (Tkinter UI, SQLite store, Docling pipeline, dated Excel workbooks) — how to decompose requests, what to verify before claiming done, and the project-specific traps. Distilled from the precheck/per-document-pipeline sessions.
---

# How I actually worked through this project

## 1. Decompress the request into numbered semantics; ask only about real forks

The user writes compressed, sometimes idiosyncratic requirement prose ("if once specific
files were analyzed for dulpication don't process the duplication only execute new files").
Restating it as numbered behaviors exposes which parts are decidable from the code and which
are genuine forks only the user can resolve.

The batch-pipeline request had 4 numbered points hiding exactly three forks: is the RAG
build always-on or opt-in; how many copy-vs-generate prompts per batch; and what "generate
new" means for a dated file (fresh dated file vs rewrite in place). I asked those three with
recommended defaults and decided everything else — precheck key set, sweep ordering, where
the locator lives — from the code. Asking more would have wasted a round trip; asking fewer
would have baked in a wrong guess about file semantics the user cares about (they keep
dated history deliberately).

## 2. Trace the pipeline end-to-end before designing; the summary lies by omission

"Load Docling only once" sounded like a caching bug. Reading `Pdf_File_processor.py`
showed the root cause was twofold: `DoclingExtractor.__init__` rebuilt the converter per
file, **and** single-file extraction runs in a `multiprocessing.Process` for timeout
isolation — a subprocess can never see a parent-process cache. One fix (shared converter)
would have silently done nothing on the subprocess path. The real design was two paths:
shared in-process converter for batches, keep subprocess+timeout for single files. Every
feature here has this shape — the obvious fix is half the fix until you've traced who
calls whom (`process_pdf_mode_file` → `process_pdf_sections` → `_extract_chunks` →
subprocess-or-inprocess).

## 3. Know the three Tk traps in this codebase

They cost real debugging time until learned, and they recur:

- **Worker threads must not touch Tk.** Everything long-running goes through
  `_run_threaded(work, ...)` — the worker writes to a `queue.Queue`, an `after()` poller
  applies it on the main thread. Modal dialogs (`askyesnocancel` for copy-vs-generate)
  must fire *before* the worker starts, on the main thread.
- **Grid weights don't give fixed fractions.** The "console = 40% of window" request
  failed with weights (got ~25%) because grid respects each widget's requested minimum.
  `place(relheight=0.4)` inside a body frame is the only thing that guarantees it.
- **The app redirects `sys.stdout` to the Tk console.** A headless test that constructs
  `AutomatedLiteratureUI` and prints results produces *nothing* — the run looked like
  `EXIT=0` with zero output. Fix: write assertions' results to a temp file and dump via
  `sys.__stdout__.write` at the end.

## 4. Verify ordering, not existence

The per-document pipeline's whole point was interleaving: doc A goes through
analyze → sync → DOI → classify before doc B starts. A test that merely checks "classify
was called" passes on the old stage-batched code too. The headless test monkeypatched
every stage to append `("stage", filename)` to one list, then asserted **index order**:

```python
assert a_analyze < seq.index("sync(docA.pdf)") < seq.index("doi(docA.pdf)")
       < seq.index("classify:title(docA.pdf)") < b_analyze
```

Same principle for the RAG checkbox: assert `generate_databases` fires exactly once,
after the sweeps, before cleanup — and *not at all* when unticked (run both cases).

## 5. Design around the fresh-batch ordering hole with graceful no-ops

On a brand-new batch, SQL rows don't exist until the registry row is written — so any
per-document step that pushes to SQL mid-loop can find nothing to update. The pattern that
works here: per-document steps no-op safely (`_push_eval_to_sql` returns False if
`get_document` is None; Excel writes dedupe via `_is_duplicate_in_sheet`), and idempotent
post-loop sweeps (`sync_storage_to_sql`, `evaluate_space`, `classify_space`) guarantee
completeness. `upsert_document`'s COALESCE on `ENRICHMENT_COLUMNS` is what makes the final
full re-sync safe — without it the sweep would wipe classification/DOI data written
minutes earlier. Any new enrichment column **must** be added to `ENRICHMENT_COLUMNS` or
it gets nulled on the next sync.

## 6. Extend signatures with optional kwargs, then grep every call site

`mode=` on classify/evaluate and `eval_mode=` on `process_pdf_mode_file` were added with
defaults matching old behavior, then:

```
grep -rn "classify_space\|evaluate_space\|process_pdf_mode_file" src/
```

That grep caught the callers a memory-based check would miss: `batch_dedup.py:320`,
`Folder_Data_Analyzer.py:150`, and the Tab-5 storage passes in `main_window.py` around
line 1129 — all of which had to keep working unchanged. This codebase has multiple entry
points into the same processors (single-file, folder, dedup batch, Tab-5 re-runs); a
signature change is never local.

## 7. Mock at the money boundary; the mock counter *is* the acceptance test

LLM calls here are rate-limited (Blablador, `time.sleep(1.5)` per call) and Docling loads
take minutes — so tests replace `classify_title`/`classify_abstract` with counting lambdas
and use temp `DataAnalyzeManager` spaces + temp DB paths. Crucially, the counter is not
just a speed hack: for the copy-from-previous feature, `calls["title"] == 0` in copy mode
**is the feature** ("don't pay for what already exists"). Also route `sql_store.DB_PATH`
to a temp file *before* constructing anything, or tests write into the user's real
`~/Automated Literature Review` store.

## 8. The verification ladder for any change here

Rungs, in order, each catching what the previous can't:
1. `./.venv/bin/python -m py_compile <every changed file>` — the venv is gitignored; never
   the system python.
2. Import sweep + `inspect.signature` checks on new params — catches lazy-import typos
   `py_compile` can't see.
3. Temp-dir unit tests (fake registry xlsx + abstract JSON + dated workbook) for backend
   logic.
4. Headless UI construct (`app.withdraw()`, stub `_ensure_api_key`, capture the `work`
   closure from `_run_threaded` and run it synchronously) for wiring and ordering.

A change is "done" at rung 4, not rung 1. The `ReviewApp(master=root)` TypeError happened
because I skipped checking the actual constructor (`__init__(self, container)`) — rung 2
thinking applied to rung 4 objects.

## 9. Respect the dated-workbook contract and centralize "where does data live"

`{date}_Title_Classification.xlsx`, `{date}_Abstract_Eval_Overview.xlsx` etc. are
append-only history: "regenerate" writes today's file and never touches prior dates;
"copy" pulls the *newest* prior row forward. Rather than scattering glob-and-read logic
across classify/evaluate/DOI, all of it went into one module (`analysis_precheck.py`:
`find_dated_files_with`, `latest_dated_row`, `document_status`) so the storage-vs-SQL
presence question has exactly one answer. When the next feature needs "does X exist
already", extend that module — don't write a new glob.

## 10. Commit cadence is user-controlled, one feature per commit

The user runs a strict loop: feature request → implementation+verification → the literal
message "commit and push". Never commit proactively, never bundle two requests into one
commit, and expect the next message after a push to be an unrelated new feature. Stale
task-list entries from prior features get deleted, not reused — the plan file may also be
stale from a previous session (Write fails on unread files; read a few lines, then
overwrite).
