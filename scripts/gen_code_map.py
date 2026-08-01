#!/usr/bin/env python3
"""
Generate ``docs/CODE_MAP.html`` — an interactive map of every module, class,
function and method in ``src/``, and how they are linked.

    ./.venv/bin/python scripts/gen_code_map.py

Two kinds of link are extracted, both by reading the tree with ``ast`` (nothing
is imported, so this runs without the ML stack):

* **imports** — module -> module, including the lazily-imported ones written
  inside functions, which is how much of this codebase avoids import cycles and
  slow start-up.
* **calls** — function/method -> function/method/class. A call is resolved to a
  definition in the same module first; failing that, to a repo-wide name if it
  is unique. Ambiguous names (the same function name defined in several
  modules) are counted and reported rather than guessed at, so no edge in the
  map is invented.

The output is one self-contained HTML file: the graph data is embedded as JSON
and the force-directed layout is plain canvas + JavaScript, with no CDN and no
network access, so it opens from disk and works offline.
"""

from __future__ import annotations

import ast
import json
import pathlib
from collections import defaultdict
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
OUT = ROOT / "docs" / "CODE_MAP.html"

LAYERS = [
    ("alr/collection", "Collection", "#4f8ef7"),
    ("alr/data_analysis", "Analysis", "#f7934f"),
    ("alr/rag_builders", "RAG / databases", "#9b6bf7"),
    ("alr/analysis_evaluation", "Evaluation", "#3fb98a"),
    ("alr/common", "Shared services", "#e8555f"),
    ("alr/ui/desktop", "Desktop UI", "#d9a83a"),
    ("alr/ui/cli", "CLI", "#7a8a99"),
]
OTHER = ("Entry points", "#8a8f98")


def layer_of(rel: str):
    for name_prefix, title, colour in LAYERS:
        if rel.startswith("src/" + name_prefix + "/"):
            return title, colour
    return OTHER


def mod_key(path: pathlib.Path) -> str:
    """'src/alr/common/sections.py' -> 'alr.common.sections'."""
    return path.relative_to(SRC).with_suffix("").as_posix().replace("/", ".")


def collect():
    files = [p for p in sorted(SRC.rglob("*.py"))
             if "__pycache__" not in p.parts and p.name != "__init__.py"]

    modules = {}
    # name -> [(module, qualname)] for every top-level def, class and method
    defined = defaultdict(list)

    trees = {}
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        key = mod_key(path)
        trees[key] = tree
        rel = path.relative_to(ROOT).as_posix()
        title, colour = layer_of(rel)
        symbols = []

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                symbols.append({"name": node.name, "kind": "class",
                                "doc": short(ast.get_docstring(node))})
                defined[node.name].append((key, node.name))
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        qual = f"{node.name}.{item.name}"
                        symbols.append({"name": qual, "kind": "method",
                                        "doc": short(ast.get_docstring(item))})
                        defined[item.name].append((key, qual))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append({"name": node.name, "kind": "function",
                                "doc": short(ast.get_docstring(node))})
                defined[node.name].append((key, node.name))

        modules[key] = {
            "id": key, "path": rel, "layer": title, "colour": colour,
            "lines": len(path.read_text(encoding="utf-8").splitlines()),
            "doc": short(ast.get_docstring(tree)),
            "symbols": symbols,
        }

    imports = defaultdict(set)     # module -> {module}
    calls = defaultdict(set)       # "mod::qual" -> {"mod::qual"}
    ambiguous = defaultdict(int)

    for key, tree in trees.items():
        own_last = {s["name"].split(".")[-1]: s["name"]
                    for s in modules[key]["symbols"]}
        # Local binding -> (defining module, original name). Built from this
        # module's own from-imports, so `from x import set_cell as _set_cell`
        # resolves `_set_cell(...)` to the real definition instead of missing
        # it (matching on the call's spelling alone cannot see through an
        # alias, and this codebase aliases deliberately).
        bindings = {}

        for node in ast.walk(tree):
            # ---- imports (module level and lazily, inside functions) ----
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name in modules:
                        imports[key].add(a.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                base = node.module
                if base in modules:
                    imports[key].add(base)
                for a in node.names:
                    cand = f"{base}.{a.name}"
                    if cand in modules:              # from pkg import module
                        imports[key].add(cand)
                    elif base in modules:            # from module import symbol
                        bindings[a.asname or a.name] = (base, a.name)

        # ---- calls, attributed to the enclosing definition ----
        for holder, body in iter_definitions(tree):
            src_id = f"{key}::{holder}"
            for node in ast.walk(body):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                if isinstance(fn, ast.Name):
                    name = fn.id
                elif isinstance(fn, ast.Attribute):
                    name = fn.attr
                else:
                    continue
                # 1. a definition in this module wins
                if name in own_last:
                    dst = f"{key}::{own_last[name]}"
                    if dst != src_id:
                        calls[src_id].add(dst)
                    continue
                # 2. an explicit import binding in this module is exact
                if name in bindings:
                    bmod, bname = bindings[name]
                    match = next((s["name"] for s in modules[bmod]["symbols"]
                                  if s["name"].split(".")[-1] == bname), None)
                    if match:
                        dst = f"{bmod}::{match}"
                        if dst != src_id:
                            calls[src_id].add(dst)
                        continue
                # 3. otherwise accept a repo-wide name only if it is unique
                hits = defined.get(name, [])
                if len(hits) == 1:
                    m, qual = hits[0]
                    dst = f"{m}::{qual}"
                    if dst != src_id:
                        calls[src_id].add(dst)
                elif len(hits) > 1:
                    ambiguous[name] += 1

    return modules, imports, calls, ambiguous


def iter_definitions(tree):
    """Yield (qualname, node) for every top-level function, class and method."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield f"{node.name}.{item.name}", item


def short(doc, limit=150):
    if not doc:
        return ""
    text = " ".join(doc.strip().split())
    if ". " in text[:limit + 60]:
        text = text.split(". ")[0] + "."
    return text if len(text) <= limit else text[:limit - 1] + "…"


def build_payload():
    modules, imports, calls, ambiguous = collect()

    # per-module call traffic, for the module-level edges
    mod_calls = defaultdict(int)
    for src, dsts in calls.items():
        smod = src.split("::")[0]
        for d in dsts:
            dmod = d.split("::")[0]
            if dmod != smod:
                mod_calls[(smod, dmod)] += 1

    edges = []
    seen = set()
    for smod, targets in imports.items():
        for dmod in targets:
            edges.append({"s": smod, "t": dmod, "kind": "import",
                          "n": mod_calls.get((smod, dmod), 0)})
            seen.add((smod, dmod))
    for (smod, dmod), n in mod_calls.items():
        if (smod, dmod) not in seen:
            edges.append({"s": smod, "t": dmod, "kind": "call", "n": n})

    # inbound/outbound per symbol, for the detail panel
    inbound = defaultdict(list)
    for src, dsts in calls.items():
        for d in dsts:
            inbound[d].append(src)

    for m in modules.values():
        for s in m["symbols"]:
            sid = f"{m['id']}::{s['name']}"
            s["out"] = sorted(calls.get(sid, []))
            s["in"] = sorted(inbound.get(sid, []))

    layers = [{"title": t, "colour": c} for _p, t, c in LAYERS]
    layers.append({"title": OTHER[0], "colour": OTHER[1]})

    return {
        "generated": date.today().isoformat(),
        "modules": list(modules.values()),
        "edges": edges,
        "layers": layers,
        "stats": {
            "modules": len(modules),
            "classes": sum(1 for m in modules.values() for s in m["symbols"] if s["kind"] == "class"),
            "methods": sum(1 for m in modules.values() for s in m["symbols"] if s["kind"] == "method"),
            "functions": sum(1 for m in modules.values() for s in m["symbols"] if s["kind"] == "function"),
            "edges": len(edges),
            "calls": sum(len(v) for v in calls.values()),
            "ambiguous": sum(ambiguous.values()),
        },
    }


HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Code map — Automated Literature Review</title>
<style>
:root{--bg:#12151a;--panel:#1a1f27;--line:#2b323d;--fg:#e7ecf3;--dim:#98a3b3;--accent:#6ea8ff}
@media (prefers-color-scheme: light){
  :root{--bg:#f6f7f9;--panel:#fff;--line:#dfe3e9;--fg:#1a1f27;--dim:#5d6875;--accent:#2f6fd0}
}
*{box-sizing:border-box}
body{margin:0;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
     background:var(--bg);color:var(--fg);overflow:hidden}
header{padding:10px 16px;border-bottom:1px solid var(--line);display:flex;gap:16px;
       align-items:center;flex-wrap:wrap;background:var(--panel)}
h1{font-size:15px;margin:0;font-weight:650}
.stats{color:var(--dim);font-size:12px}
input[type=search]{background:var(--bg);border:1px solid var(--line);color:var(--fg);
       border-radius:6px;padding:5px 9px;min-width:220px;font-size:13px}
.legend{display:flex;gap:10px;flex-wrap:wrap;font-size:12px;color:var(--dim)}
.legend span{display:inline-flex;align-items:center;gap:5px;cursor:pointer;user-select:none}
.legend i{width:10px;height:10px;border-radius:50%;display:inline-block}
.legend .off{opacity:.35;text-decoration:line-through}
main{display:flex;height:calc(100vh - 53px)}
#stage{flex:1;position:relative;min-width:0}
canvas{display:block;width:100%;height:100%;cursor:grab}
canvas.drag{cursor:grabbing}
aside{width:400px;max-width:46vw;border-left:1px solid var(--line);background:var(--panel);
      overflow-y:auto;padding:14px 16px}
aside h2{font-size:14px;margin:0 0 2px}
aside .path{color:var(--dim);font-size:12px;font-family:ui-monospace,Menlo,Consolas,monospace;
      word-break:break-all;margin-bottom:8px}
aside .doc{color:var(--fg);font-size:13px;margin:8px 0 12px}
.sec{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--dim);
     margin:16px 0 6px;border-top:1px solid var(--line);padding-top:10px}
.sym{border:1px solid var(--line);border-radius:7px;padding:7px 9px;margin-bottom:6px;background:var(--bg)}
.sym b{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px;font-weight:600}
.kind{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim);margin-left:6px}
.sym p{margin:3px 0 0;font-size:12px;color:var(--dim)}
.links{margin-top:5px;font-size:11.5px}
.links div{margin-top:2px}
.links a{color:var(--accent);text-decoration:none;cursor:pointer;
     font-family:ui-monospace,Menlo,Consolas,monospace;word-break:break-word}
.links div{overflow-wrap:anywhere}
.links a:hover{text-decoration:underline}
.chip{display:inline-block;background:var(--bg);border:1px solid var(--line);border-radius:20px;
      padding:1px 8px;margin:2px 3px 0 0;font-size:11px;cursor:pointer}
.chip:hover{border-color:var(--accent)}
.hint{color:var(--dim);font-size:12px}
.tip{position:absolute;pointer-events:none;background:var(--panel);border:1px solid var(--line);
     border-radius:6px;padding:5px 8px;font-size:12px;display:none;max-width:320px}
button.reset{background:var(--bg);border:1px solid var(--line);color:var(--fg);
     border-radius:6px;padding:5px 10px;cursor:pointer;font-size:12px}
button.reset:hover{border-color:var(--accent)}
</style></head><body>
<header>
  <h1>Code map</h1>
  <span class="stats" id="stats"></span>
  <input type="search" id="q" placeholder="Search module, class, function…">
  <button class="reset" id="reset">Reset view</button>
  <span class="legend" id="legend"></span>
</header>
<main>
  <div id="stage"><canvas id="c"></canvas><div class="tip" id="tip"></div></div>
  <aside id="side"><p class="hint">Each circle is a module, sized by its length and
  coloured by layer. A line means one module imports or calls into another.<br><br>
  <b>Click</b> a module to list its classes, functions and methods, with what each one
  calls and what calls it. <b>Drag</b> the background to pan, <b>scroll</b> to zoom,
  <b>drag</b> a circle to pin it. Click a layer in the legend to hide it.</p></aside>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const cv = document.getElementById('c'), ctx = cv.getContext('2d');
const tip = document.getElementById('tip'), side = document.getElementById('side');
document.getElementById('stats').textContent =
  `${DATA.stats.modules} modules · ${DATA.stats.classes} classes · ${DATA.stats.methods} methods · `
  + `${DATA.stats.functions} functions · ${DATA.stats.edges} module links · ${DATA.stats.calls} resolved calls`;

const byId = new Map(DATA.modules.map(m => [m.id, m]));
const hidden = new Set();
const nodes = DATA.modules.map((m, i) => {
  const a = i / DATA.modules.length * Math.PI * 2;
  return {...m, x: Math.cos(a) * 300 + Math.random() * 40,
                y: Math.sin(a) * 300 + Math.random() * 40, vx: 0, vy: 0,
                r: Math.max(5, Math.min(26, Math.sqrt(m.lines) * 0.75)), pinned: false};
});
const nodeById = new Map(nodes.map(n => [n.id, n]));
const edges = DATA.edges.filter(e => nodeById.has(e.s) && nodeById.has(e.t))
                        .map(e => ({...e, a: nodeById.get(e.s), b: nodeById.get(e.t)}));

const deg = new Map();
edges.forEach(e => { deg.set(e.s, (deg.get(e.s) || 0) + 1); deg.set(e.t, (deg.get(e.t) || 0) + 1); });

// ---- legend -------------------------------------------------------------
const legend = document.getElementById('legend');
DATA.layers.forEach(l => {
  const s = document.createElement('span');
  s.innerHTML = `<i style="background:${l.colour}"></i>${l.title}`;
  s.onclick = () => { hidden.has(l.title) ? hidden.delete(l.title) : hidden.add(l.title);
                      s.classList.toggle('off'); draw(); };
  legend.appendChild(s);
});
const visible = n => !hidden.has(n.layer);

// ---- force layout -------------------------------------------------------
let alpha = 1;
function step(){
  const k = 0.0009;
  for (const n of nodes){ n.vx -= n.x * k; n.vy -= n.y * k; }      // gentle centring
  for (let i = 0; i < nodes.length; i++){
    for (let j = i + 1; j < nodes.length; j++){
      const a = nodes[i], b = nodes[j];
      let dx = b.x - a.x, dy = b.y - a.y, d2 = dx*dx + dy*dy || 1;
      const rep = 2600 / d2;
      const d = Math.sqrt(d2); dx /= d; dy /= d;
      a.vx -= dx * rep; a.vy -= dy * rep; b.vx += dx * rep; b.vy += dy * rep;
    }
  }
  for (const e of edges){
    const dx = e.b.x - e.a.x, dy = e.b.y - e.a.y;
    const d = Math.hypot(dx, dy) || 1, f = (d - 150) * 0.0035;
    const ux = dx / d * f, uy = dy / d * f;
    e.a.vx += ux; e.a.vy += uy; e.b.vx -= ux; e.b.vy -= uy;
  }
  for (const n of nodes){
    if (n.pinned) { n.vx = n.vy = 0; continue; }
    n.x += (n.vx *= 0.82) * alpha; n.y += (n.vy *= 0.82) * alpha;
  }
  alpha = Math.max(0.02, alpha * 0.995);
}

// ---- view ---------------------------------------------------------------
let scale = 1, ox = 0, oy = 0, hover = null, selected = null, dragNode = null, panning = false;
function resize(){
  const r = cv.parentElement.getBoundingClientRect(), dpr = devicePixelRatio || 1;
  cv.width = r.width * dpr; cv.height = r.height * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); W = r.width; H = r.height;
}
let W = 0, H = 0;
const toScreen = n => [n.x * scale + ox + W / 2, n.y * scale + oy + H / 2];

function draw(){
  ctx.clearRect(0, 0, W, H);
  const lit = new Set();
  const focus = hover || selected;
  if (focus) { lit.add(focus.id);
    edges.forEach(e => { if (e.s === focus.id) lit.add(e.t); if (e.t === focus.id) lit.add(e.s); }); }

  for (const e of edges){
    if (!visible(e.a) || !visible(e.b)) continue;
    const on = focus && (e.s === focus.id || e.t === focus.id);
    ctx.strokeStyle = on ? 'rgba(110,168,255,.85)' : 'rgba(140,150,165,.16)';
    ctx.lineWidth = on ? 1.6 : (e.kind === 'import' ? 0.7 : 0.5);
    if (e.kind === 'call' && !on) ctx.setLineDash([3, 3]); else ctx.setLineDash([]);
    const [ax, ay] = toScreen(e.a), [bx, by] = toScreen(e.b);
    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
  }
  ctx.setLineDash([]);
  for (const n of nodes){
    if (!visible(n)) continue;
    const [x, y] = toScreen(n), r = n.r * Math.max(0.6, Math.min(1.6, scale));
    const dim = focus && !lit.has(n.id);
    ctx.globalAlpha = dim ? 0.22 : 1;
    ctx.beginPath(); ctx.arc(x, y, r, 0, 6.284);
    ctx.fillStyle = n.colour; ctx.fill();
    if (selected && n.id === selected.id){ ctx.lineWidth = 2.5; ctx.strokeStyle = '#fff'; ctx.stroke(); }
    if (!dim && (scale > 0.75 || (deg.get(n.id) || 0) > 6 || n === focus)){
      ctx.globalAlpha = dim ? 0.2 : 0.92;
      ctx.fillStyle = getComputedStyle(document.body).color;
      ctx.font = '11px ui-monospace, Menlo, Consolas, monospace';
      ctx.textAlign = 'center';
      ctx.fillText(n.id.split('.').pop(), x, y - r - 4);
    }
    ctx.globalAlpha = 1;
  }
}
function tick(){ step(); draw(); requestAnimationFrame(tick); }

function at(px, py){
  for (let i = nodes.length - 1; i >= 0; i--){
    const n = nodes[i]; if (!visible(n)) continue;
    const [x, y] = toScreen(n);
    if (Math.hypot(px - x, py - y) <= n.r * Math.max(0.6, Math.min(1.6, scale)) + 3) return n;
  }
  return null;
}

// ---- interaction --------------------------------------------------------
let last = null;
cv.addEventListener('mousemove', ev => {
  const r = cv.getBoundingClientRect(), px = ev.clientX - r.left, py = ev.clientY - r.top;
  if (dragNode){ dragNode.x = (px - ox - W/2)/scale; dragNode.y = (py - oy - H/2)/scale;
                 dragNode.pinned = true; alpha = Math.max(alpha, .25); return; }
  if (panning && last){ ox += px - last[0]; oy += py - last[1]; last = [px, py]; return; }
  hover = at(px, py);
  if (hover){ tip.style.display = 'block'; tip.style.left = (px + 14) + 'px';
              tip.style.top = (py + 12) + 'px';
              tip.innerHTML = `<b>${hover.id}</b><br>${hover.doc || ''}`
                + `<br><span style="opacity:.65">${hover.lines} lines · `
                + `${hover.symbols.length} definitions · ${deg.get(hover.id)||0} links</span>`; }
  else tip.style.display = 'none';
});
cv.addEventListener('mousedown', ev => {
  const r = cv.getBoundingClientRect(), px = ev.clientX - r.left, py = ev.clientY - r.top;
  const n = at(px, py);
  if (n) dragNode = n; else { panning = true; last = [px, py]; cv.classList.add('drag'); }
});
addEventListener('mouseup', () => { dragNode = null; panning = false; cv.classList.remove('drag'); });
cv.addEventListener('click', ev => {
  const r = cv.getBoundingClientRect();
  const n = at(ev.clientX - r.left, ev.clientY - r.top);
  if (n) show(n.id); else { selected = null; draw(); }
});
cv.addEventListener('wheel', ev => {
  ev.preventDefault();
  const f = ev.deltaY < 0 ? 1.12 : 1 / 1.12;
  scale = Math.max(0.2, Math.min(4, scale * f));
}, {passive:false});
document.getElementById('reset').onclick = () => {
  scale = 1; ox = oy = 0; alpha = 1; nodes.forEach(n => n.pinned = false);
  selected = null; side.scrollTop = 0;
};

// ---- detail panel -------------------------------------------------------
function link(id){
  const [m, q] = id.split('::');
  return `<a onclick="show('${m}','${q||''}')">${m.split('.').pop()}.${q||''}</a>`;
}
function show(modId, focusSym){
  const m = byId.get(modId); if (!m) return;
  selected = nodeById.get(modId) || null;
  const order = {class: 0, method: 1, function: 2};
  const syms = [...m.symbols].sort((a, b) => order[a.kind] - order[b.kind]
                                          || a.name.localeCompare(b.name));
  const outMods = new Set(), inMods = new Set();
  m.symbols.forEach(s => { s.out.forEach(o => outMods.add(o.split('::')[0]));
                           s.in.forEach(i => inMods.add(i.split('::')[0])); });
  outMods.delete(modId); inMods.delete(modId);

  side.innerHTML = `<h2>${m.id}</h2><div class="path">${m.path}</div>`
    + (m.doc ? `<div class="doc">${m.doc}</div>` : '')
    + `<div class="hint">${m.lines} lines · ${m.symbols.length} definitions · `
    + `<span style="color:${m.colour}">${m.layer}</span></div>`
    + (outMods.size ? `<div class="sec">Calls into</div>`
        + [...outMods].sort().map(x => `<span class="chip" onclick="show('${x}')">${x}</span>`).join('') : '')
    + (inMods.size ? `<div class="sec">Called by</div>`
        + [...inMods].sort().map(x => `<span class="chip" onclick="show('${x}')">${x}</span>`).join('') : '')
    + `<div class="sec">Definitions</div>`
    + syms.map(s => {
        const hl = focusSym && s.name === focusSym ? ' style="border-color:var(--accent)"' : '';
        return `<div class="sym"${hl} id="s-${s.name.replace(/\\W/g,'_')}">`
          + `<b>${s.name}</b><span class="kind">${s.kind}</span>`
          + (s.doc ? `<p>${s.doc}</p>` : '')
          + `<div class="links">`
          + (s.out.length ? `<div>→ calls ${s.out.map(link).join(', ')}</div>` : '')
          + (s.in.length ? `<div>← called by ${s.in.map(link).join(', ')}</div>` : '')
          + (!s.out.length && !s.in.length ? `<div class="hint">no resolved links</div>` : '')
          + `</div></div>`;
      }).join('');
  if (focusSym){
    const el = document.getElementById('s-' + focusSym.replace(/\\W/g, '_'));
    if (el) side.scrollTop = el.offsetTop - side.offsetTop - 60;
  } else side.scrollTop = 0;
}
window.show = show;

// ---- search -------------------------------------------------------------
document.getElementById('q').addEventListener('input', ev => {
  const q = ev.target.value.trim().toLowerCase();
  if (!q) return;
  for (const m of DATA.modules){
    if (m.id.toLowerCase().includes(q)) { show(m.id); return; }
    const hit = m.symbols.find(s => s.name.toLowerCase().includes(q));
    if (hit) { show(m.id, hit.name); return; }
  }
});

addEventListener('resize', () => { resize(); draw(); });
resize(); tick();
</script></body></html>
"""


def main() -> int:
    payload = build_payload()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(HTML.replace("__DATA__", json.dumps(payload)), encoding="utf-8")
    s = payload["stats"]
    print(f"Wrote {OUT.relative_to(ROOT)}: {s['modules']} modules, {s['edges']} module links, "
          f"{s['calls']} resolved calls, {s['ambiguous']} call sites skipped as ambiguous.")
    print(f"Size: {OUT.stat().st_size / 1024:.0f} KB (self-contained, opens offline).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
