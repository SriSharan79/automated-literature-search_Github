#!/usr/bin/env python3
"""
Generate ``docs/CODE_REFERENCE.md`` — every module, class, method and function
in ``src/``, with a one-line description of each.

Run it after adding or renaming anything so the reference cannot drift:

    ./.venv/bin/python scripts/gen_code_reference.py

Descriptions come from docstrings where they exist. Modules that predate the
docstring habit get their purpose from :data:`MODULE_PURPOSE` below — the one
place to edit when a module's job changes (a module missing from both is
listed as undocumented in the generated file, which is the nudge to fix it).

Nothing is imported: the tree is read with ``ast``, so this is safe to run
without the heavy ML dependencies installed.
"""

from __future__ import annotations

import ast
import pathlib
import sys
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
OUT = ROOT / "docs" / "CODE_REFERENCE.md"

# Package -> (heading, what this layer is for). Order = reading order.
LAYERS = [
    ("alr/collection", "Collection layer",
     "Turning a research question into a ranked list of publications to read."),
    ("alr/data_analysis", "Analysis layer",
     "Turning a PDF into structured, per-document analyzed data."),
    ("alr/rag_builders", "RAG / database layer",
     "Turning analyzed data into searchable text + vector databases and reports."),
    ("alr/analysis_evaluation", "Evaluation layer",
     "Scoring how well the analyzed data is supported by the source text."),
    ("alr/common", "Shared services",
     "Storage layout, Excel/JSON/SQL access, LLM plumbing, logging and cleanup."),
    ("alr/ui/desktop", "Desktop UI",
     "The Tkinter main window, the Review tool and their shared widgets."),
    ("alr/ui/cli", "Command-line UI",
     "Terminal front-ends for the same pipeline."),
]

# Purpose lines for modules with no docstring. Keep these one sentence.
MODULE_PURPOSE = {
    "src/Main.py": "Legacy top-level entry point kept for the original CLI flow.",
    "src/gui_main.py": "Entry point for the main desktop application (packaged as AutomatedLiteratureReview).",
    "src/review_main.py": "Entry point for the standalone Review tool (packaged as ReviewTool).",

    # --- collection ---
    "src/alr/collection/Phrase_Classification_Utils.py":
        "Scores and ranks generated search phrases against the research area/question.",
    "src/alr/collection/collection_system_prompts.py":
        "System prompts for scope generation, keyword suggestion and phrase generation.",
    "src/alr/collection/keyword_sorting_Utils.py":
        "Sorts and de-duplicates keyword lists before phrase generation.",
    "src/alr/collection/search_phrase_generator_config.py":
        "Tunables for the search-phrase generator (counts, ranking bases, output columns).",
    "src/alr/collection/search_phrase_generator_logger.py":
        "Writes the per-search keyword/phrase workbooks and their dated history.",
    "src/alr/collection/search_phrase_generator_utils.py":
        "Builds search phrases from selected keywords and ranks them four ways.",

    # --- data analysis ---
    "src/alr/data_analysis/Abstract_Analyzer.py":
        "Extracts the seven abstract attributes from an identified abstract via the LLM.",
    "src/alr/data_analysis/Data_analysis_system_prompts.py":
        "System prompts for abstract, introduction, results & conclusion and reference extraction.",
    "src/alr/data_analysis/Data_sorting_utils.py":
        "Sorts Docling chunks into named sections and writes the per-document section logs.",
    "src/alr/data_analysis/File_Data_extraction_with_Docling.py":
        "Runs Docling over one PDF and caches its chunks, with a per-file timeout.",
    "src/alr/data_analysis/Folder_Data_Analyzer.py":
        "CLI batch driver: analyze every PDF in a folder, then sync the space into SQL.",
    "src/alr/data_analysis/Introduction_Analyzer.py":
        "Extracts Background / Motivation / Gaps & Limitations / RQs & Scope from the introduction.",
    "src/alr/data_analysis/LLM_Reference_Extractor.py":
        "Parses the reference section's chunks into structured citation records.",
    "src/alr/data_analysis/Pdf_File_processor.py":
        "The per-document pipeline: sectioning, the three analyzers, references, logs and registry.",
    "src/alr/data_analysis/Refrences_log_utils.py":
        "Reads and writes the extracted-references workbook.",
    "src/alr/data_analysis/Results_Conclusion_Analyzer.py":
        "Extracts the five Results & Conclusion attributes from the identified section text.",
    "src/alr/data_analysis/Table_image_extractor.py":
        "Exports tables and figures from a PDF and owns the shared Docling converter.",
    "src/alr/data_analysis/title_extracter.py":
        "Determines a PDF's real title (DOI/arXiv lookup, then LLM disambiguation), memoized per file.",

    # --- rag builders ---
    "src/alr/rag_builders/db_manager.py":
        "Builds the per-section text and vector databases for a space, and the common multi-space DB.",
    "src/alr/rag_builders/master_excel_db_builder.py":
        "Builds the combined master Excel workbook, one sheet per analyzed attribute.",
    "src/alr/rag_builders/query_executor.py":
        "Runs a RAG query over the section databases and writes the query report workbook.",
    "src/alr/rag_builders/text_db_updater.py":
        "Appends analyzed attribute values into the per-section text databases (Excel + JSON).",
    "src/alr/rag_builders/vector_db_updater.py":
        "Embeds strings and maintains the per-section FAISS indexes.",

    # --- evaluation ---
    "src/alr/analysis_evaluation/Distance_w_Structural _Alignment.py":
        "Levenshtein distance/ratio and word error rate (note: the filename contains a space).",
    "src/alr/analysis_evaluation/Lexical_Overlap_Metrics.py":
        "Jaccard, ROUGE-1/2/L and BLEU between a reference and a candidate string.",
    "src/alr/analysis_evaluation/data_evaluator.py":
        "Grounding evaluation: how much of each extracted attribute is supported by the source text.",
    "src/alr/analysis_evaluation/publication_classification/Classification_logic_with_Q's.py":
        "Question-scored classification: asks a fixed question set per document and scores the answers.",
    "src/alr/analysis_evaluation/publication_classification/classification_questions.py":
        "The question bank and prompt builders used by question-scored classification.",
    "src/alr/analysis_evaluation/publication_classification/title_classifier.py":
        "Classifies a publication from its title (and the custom-topic prompt builder).",

    # --- common ---
    "src/alr/common/LLM_Config.py":
        "Provider/model registry, API-key storage and the embedding-backend selection.",
    "src/alr/common/System_prompts.py":
        "Prompts shared across more than one pipeline stage.",
    "src/alr/common/excel_utils.py":
        "Cached Excel reads/writes, deferred write sessions, cell-safe updates and checkpointing.",
    "src/alr/common/file_handlers.py":
        "Path safety (length-guarded names), JSON indexing under a search root, file moves.",
    "src/alr/common/file_manager.py":
        "The storage-space layout: every folder and file path the pipeline reads or writes.",
    "src/alr/common/general_utils.py":
        "Small shared helpers: timing, set differences, text normalization, folder cleaning.",
    "src/alr/common/json_utils.py":
        "Reading and writing the per-document analysis and section JSON files.",
    "src/alr/common/llm_utils.py":
        "All LLM and embedding calls: providers, timeouts, bounded retries, rate limiting, logging.",

    # --- ui ---
    "src/alr/ui/cli/Data_analysis_UI.py": "Terminal menu for the analysis stage.",
    "src/alr/ui/cli/collection_ui.py": "Terminal menu for the collection stage.",
    "src/alr/ui/cli/pipeline.py": "Terminal driver chaining collection -> analysis -> databases.",
    "src/alr/ui/desktop/main_window.py":
        "The main desktop application: all five tabs, the shared console and every action handler.",
    "src/alr/ui/desktop/section_rewriter_view.py":
        "Section Editor panel: review and correct a document's identified section text.",
}

SKIP_NAMES = {"__init__.py"}


def summarize(node) -> str:
    doc = ast.get_docstring(node) or ""
    if not doc:
        return ""
    text = " ".join(doc.strip().split())
    # First sentence, but keep it useful if the first sentence is very short.
    for end in (". ", "; "):
        if end in text[:400]:
            head = text.split(end)[0]
            if len(head) > 40:
                return head + "."
    return text if len(text) <= 400 else text[:397] + "…"


def signature(fn) -> str:
    a = fn.args
    parts = [p.arg for p in a.posonlyargs + a.args]
    if a.vararg:
        parts.append("*" + a.vararg.arg)
    parts += [p.arg for p in a.kwonlyargs]
    if a.kwarg:
        parts.append("**" + a.kwarg.arg)
    return "(" + ", ".join(parts) + ")"


def parse_module(path: pathlib.Path) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    rel = path.relative_to(ROOT).as_posix()
    mod = {
        "path": rel,
        "name": path.stem,
        "lines": len(path.read_text(encoding="utf-8").splitlines()),
        "doc": summarize(tree) or MODULE_PURPOSE.get(rel, ""),
        "documented": bool(ast.get_docstring(tree)) or rel in MODULE_PURPOSE,
        "classes": [],
        "functions": [],
    }
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            cls = {"name": node.name, "doc": summarize(node),
                   "bases": [ast.unparse(b) for b in node.bases], "methods": []}
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    cls["methods"].append({
                        "name": item.name, "sig": signature(item), "doc": summarize(item),
                    })
            mod["classes"].append(cls)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            mod["functions"].append({
                "name": node.name, "sig": signature(node), "doc": summarize(node),
            })
    return mod


def layer_of(rel: str) -> int:
    for i, (prefix, _t, _d) in enumerate(LAYERS):
        if rel.startswith("src/" + prefix):
            return i
    return len(LAYERS)  # entry points / uncategorised


def render(mods) -> str:
    n_cls = sum(len(m["classes"]) for m in mods)
    n_meth = sum(len(c["methods"]) for m in mods for c in m["classes"])
    n_fn = sum(len(m["functions"]) for m in mods)
    out = [
        "# Code reference",
        "",
        f"*Generated by `scripts/gen_code_reference.py` on {date.today().isoformat()} — "
        "do not edit by hand; re-run the script instead.*",
        "",
        f"**{len(mods)} modules · {n_cls} classes · {n_meth} methods · "
        f"{n_fn} module-level functions.**",
        "",
        "Every public and private definition in `src/` is listed. One-line "
        "descriptions come from the code's own docstrings; modules that have "
        "none get their purpose from the generator's `MODULE_PURPOSE` map.",
        "",
        "For *how the pieces fit together*, read [USER_MANUAL.md](USER_MANUAL.md); "
        "for the exhaustive behaviour notes, [FUNCTIONALITY_AUDIT.md](FUNCTIONALITY_AUDIT.md).",
        "",
        "---",
        "",
    ]

    by_layer = {}
    for m in mods:
        by_layer.setdefault(layer_of(m["path"]), []).append(m)

    for i, (prefix, title, blurb) in enumerate(LAYERS):
        group = sorted(by_layer.get(i, []), key=lambda m: m["path"])
        if not group:
            continue
        out += [f"## {title}", "", f"*{blurb}* — `{prefix}/`", ""]
        out += render_modules(group)

    rest = sorted(by_layer.get(len(LAYERS), []), key=lambda m: m["path"])
    if rest:
        out += ["## Entry points", "",
                "*Scripts that start the application.*", ""]
        out += render_modules(rest)

    undoc = [m["path"] for m in mods if not m["documented"]]
    if undoc:
        out += ["---", "", "## Modules still without a description", "",
                "These have neither a docstring nor a `MODULE_PURPOSE` entry:", ""]
        out += [f"- `{p}`" for p in sorted(undoc)]
        out += [""]
    return "\n".join(out)


def render_modules(group) -> list:
    out = []
    for m in group:
        out.append(f"### `{m['path']}`")
        out.append("")
        out.append(f"{m['doc'] or '_No description yet._'}  ")
        out.append(f"*{m['lines']} lines · {len(m['classes'])} classes · "
                   f"{len(m['functions'])} functions*")
        out.append("")
        for c in m["classes"]:
            bases = f" ({', '.join(c['bases'])})" if c["bases"] else ""
            out.append(f"**class `{c['name']}`{bases}** — {c['doc'] or '_no docstring_'}")
            out.append("")
            if c["methods"]:
                out.append("| Method | Purpose |")
                out.append("| --- | --- |")
                for meth in c["methods"]:
                    doc = (meth["doc"] or "").replace("|", "\\|") or "—"
                    out.append(f"| `{meth['name']}{meth['sig']}` | {doc} |")
                out.append("")
        if m["functions"]:
            out.append("| Function | Purpose |")
            out.append("| --- | --- |")
            for fn in m["functions"]:
                doc = (fn["doc"] or "").replace("|", "\\|") or "—"
                out.append(f"| `{fn['name']}{fn['sig']}` | {doc} |")
            out.append("")
    return out


def main() -> int:
    paths = [p for p in sorted(SRC.rglob("*.py"))
             if "__pycache__" not in p.parts and p.name not in SKIP_NAMES]
    mods = []
    for p in paths:
        try:
            mods.append(parse_module(p))
        except SyntaxError as e:
            print(f"!! could not parse {p}: {e}", file=sys.stderr)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(mods), encoding="utf-8")
    undoc = sum(1 for m in mods if not m["documented"])
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(mods)} modules, {undoc} without a description.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
