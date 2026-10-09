"""Рисует карту процессов из _map.md в SVG и интерактивную HTML-страницу.

Только стандартная библиотека. Вызывается из `python3 tools/kb.py map`.
Связи берутся из блока mermaid в _map.md, статусы и владельцы — из файлов шагов и журнала.
"""
from __future__ import annotations

import html
import json
import re

NODE_W, NODE_H = 236, 104
TERM_W, TERM_H = 176, 46
GAP_X, GAP_Y = 84, 30
PAD = 28

STATUS_CLASS = {
    "Черновик": "s-draft",
    "Готов к взятию": "s-ready",
    "Разбор": "s-explore",
    "Сверка с бизнесом": "s-check",
    "Нарезка": "s-slice",
    "Приёмка": "s-accept",
    "Декомпозиты в работе": "s-work",
    "Готово": "s-done",
}
STATUS_ORDER = list(STATUS_CLASS)

NODE_RE = r"([A-Za-z_][\w]*)\s*(\(\(\s*\"?(.*?)\"?\s*\)\)|\[\s*\"?(.*?)\"?\s*\]|\(\s*\"?(.*?)\"?\s*\)|\{\s*\"?(.*?)\"?\s*\})?"
EDGE_RE = re.compile(
    r"^\s*" + NODE_RE +
    r"\s*(-\.\s*(.+?)\s*\.->|-\.->|--\s*(.+?)\s*-->|-->\s*\|\s*(.+?)\s*\||-->|==>)\s*" +
    NODE_RE + r"\s*$"
)
NODE_ONLY_RE = re.compile(r"^\s*" + NODE_RE + r"\s*$")


# ---------- разбор mermaid ----------

def parse_mermaid(text: str):
    m = re.search(r"```mermaid\s*\n(.*?)```", text, re.S)
    if not m:
        return {}, []
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def node(mid, shape, l1, l2, l3, l4):
        label = next((x for x in (l1, l2, l3, l4) if x), None)
        n = nodes.setdefault(mid, {"id": mid, "label": mid, "terminal": False})
        if label:
            n["label"] = label.strip()
        if shape and shape.startswith("(("):
            n["terminal"] = True
        return mid

    raw_lines = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith(("flowchart", "graph", "%%", "classDef", "class ", "style ", "linkStyle", "subgraph", "direction")) \
                or line == "end":
            continue
        # A --> B & C  →  две строки
        if " & " in line and ("-->" in line or ".->" in line):
            head, _, tail = line.rpartition("-->") if "-->" in line else line.rpartition(".->")
            op = "-->" if "-->" in line else ".->"
            for part in tail.split(" & "):
                raw_lines.append(f"{head}{op} {part.strip()}")
            continue
        raw_lines.append(line)
    for line in raw_lines:
        e = EDGE_RE.match(line)
        if e:
            g = e.groups()
            a = node(*g[0:6])
            op, lab_dot, lab_dash, lab_pipe = g[6], g[7], g[8], g[9]
            b = node(*g[10:16])
            dashed = op.startswith("-.")
            edges.append({"from": a, "to": b, "dashed": dashed, "label": (lab_dot or lab_dash or lab_pipe or "").strip()})
            continue
        n = NODE_ONLY_RE.match(line)
        if n:
            node(*n.groups()[0:6])
    for n in nodes.values():
        sm = re.match(r"^(PM-\d{2})\b\s*(.*)$", n["label"])
        n["step"] = sm.group(1) if sm else None
        n["title"] = sm.group(2) if sm else n["label"]
    return nodes, edges


# ---------- раскладка ----------

def layout(nodes: dict, edges: list):
    ids = list(nodes)
    succ = {i: [] for i in ids}
    pred = {i: [] for i in ids}
    for e in edges:
        succ[e["from"]].append(e["to"])
        pred[e["to"]].append(e["from"])
    # убрать циклы: обратные рёбра DFS не участвуют в слоях
    back, state = set(), {}

    def dfs(u):
        state[u] = 1
        for v in succ[u]:
            if state.get(v) == 1:
                back.add((u, v))
            elif v not in state:
                dfs(v)
        state[u] = 2

    for i in ids:
        if i not in state:
            dfs(i)
    layer = {i: 0 for i in ids}
    changed = True
    while changed:
        changed = False
        for e in edges:
            a, b = e["from"], e["to"]
            if (a, b) in back:
                continue
            if layer[b] < layer[a] + 1:
                layer[b] = layer[a] + 1
                changed = True
    # концы пути — сразу за источником, а не в самом конце
    for i in ids:
        if nodes[i]["terminal"] and pred[i]:
            layer[i] = max(layer[p] for p in pred[i]) + 1
    cols: dict[int, list[str]] = {}
    for i in ids:
        cols.setdefault(layer[i], []).append(i)
    order = {i: k for k, i in enumerate(ids)}
    pos = {}
    for _ in range(4):
        for c in sorted(cols):
            for i in cols[c]:
                ps = [pos[p] for p in pred[i] if p in pos and layer[p] < c]
                order[i] = sum(ps) / len(ps) if ps else order[i]
            cols[c].sort(key=lambda i: (nodes[i]["terminal"], order[i]))
            for k, i in enumerate(cols[c]):
                pos[i] = k
    # промежуток перед колонкой — чтобы поместились подписи входящих рёбер
    need = {}
    for e in edges:
        if e["label"] and layer[e["to"]] > layer[e["from"]]:
            c = layer[e["to"]]
            need[c] = max(need.get(c, 0), label_w(e["label"]) + 36)
    geo = {}
    col_x, x = {}, PAD
    for c in sorted(cols):
        if c in need and c != min(cols):
            x += max(0, need[c] - GAP_X)
        col_x[c] = x
        w = max((TERM_W if nodes[i]["terminal"] else NODE_W) for i in cols[c])
        x += w + GAP_X
    for c, members in cols.items():
        y = PAD
        for i in members:
            w, h = (TERM_W, TERM_H) if nodes[i]["terminal"] else (NODE_W, NODE_H)
            geo[i] = (col_x[c], y, w, h)
            y += h + GAP_Y
    width = max((g[0] + g[2] for g in geo.values()), default=0) + PAD
    height = max((g[1] + g[3] for g in geo.values()), default=0) + PAD
    return geo, width, height, back


# ---------- рисование ----------

def short_label(text: str) -> str:
    return text if len(text) <= 34 else text[:33] + "…"


def label_w(text: str) -> int:
    return int(6.6 * len(short_label(text))) + 14


def wrap(text: str, width: int, lines: int) -> list[str]:
    words, out, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + (1 if cur else 0) <= width:
            cur = (cur + " " + w).strip()
        else:
            out.append(cur)
            cur = w
    if cur:
        out.append(cur)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = out[-1][: width - 1].rstrip() + "…"
    return out


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def svg_markup(nodes, edges, geo, width, height, back, roots, pm00=None, title=""):
    p = []
    p.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
             f'role="img" aria-label="{esc(title)}" font-family="system-ui, -apple-system, Segoe UI, Roboto, sans-serif">')
    p.append('<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
             '<path d="M0,0 L10,5 L0,10 z" class="arrowhead"/></marker></defs>')
    # рёбра
    for e in edges:
        a, b = geo[e["from"]], geo[e["to"]]
        x1, y1 = a[0] + a[2], a[1] + a[3] / 2
        x2, y2 = b[0], b[1] + b[3] / 2
        cls = "edge dashed" if e["dashed"] else "edge"
        if x2 <= x1:  # обратная связь — дугой снизу
            yb = max(a[1] + a[3], b[1] + b[3]) + 24
            d = f"M{a[0] + a[2] / 2},{a[1] + a[3]} C{a[0] + a[2] / 2},{yb} {b[0] + b[2] / 2},{yb} {b[0] + b[2] / 2},{b[1] + b[3]}"
            lx, ly = (a[0] + b[0] + a[2]) / 2, yb
        else:
            # кривая в два колена: сначала к промежутку перед целью, потом горизонтально в цель
            gap_start = x2 - max(GAP_X, label_w(e["label"]) + 36 if e["label"] else GAP_X)
            xm = max(x1 + 20, gap_start)
            dx = max(20, (xm - x1) / 2)
            d = (f"M{x1},{y1} C{x1 + dx},{y1} {xm - dx},{y2} {xm},{y2} L{x2},{y2}" if xm > x1 + 20
                 else f"M{x1},{y1} C{x1 + 40},{y1} {x2 - 40},{y2} {x2},{y2}")
            lx, ly = (xm + x2) / 2, y2 - 1
        p.append(f'<path class="{cls}" d="{d}" marker-end="url(#arr)"/>')
        if e["label"]:
            lab = short_label(e["label"])
            w = label_w(e["label"])
            p.append(f'<g class="elabel"><rect x="{lx - w / 2:.0f}" y="{ly - 10:.0f}" width="{w}" height="18" rx="4"/>'
                     f'<text x="{lx:.0f}" y="{ly + 3:.0f}" text-anchor="middle">{esc(lab)}</text></g>')
    # узлы
    for i, n in nodes.items():
        x, y, w, h = geo[i]
        if n["terminal"]:
            tl = wrap(n["title"], 24, 2)
            p.append(f'<g class="term"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{h / 2}"/>')
            for k, line in enumerate(tl):
                ty = y + h / 2 + 4 + (k - (len(tl) - 1) / 2) * 14
                p.append(f'<text x="{x + w / 2}" y="{ty:.0f}" text-anchor="middle">{esc(line)}</text>')
            p.append("</g>")
            continue
        r = roots.get(n["step"]) if n["step"] else None
        st = r["статус"] if r else "нет файла"
        cls = STATUS_CLASS.get(st, "s-missing")
        owner = r["владелец"] if r else "—"
        title = r["шаг"] if r else n["title"]
        sid = n["step"] or i
        p.append(f'<g class="node {cls}" data-step="{esc(sid)}" tabindex="0">')
        p.append(f'<title>{esc(sid)} {esc(title)} — {esc(st)}</title>')
        p.append(f'<rect class="box" x="{x}" y="{y}" width="{w}" height="{h}" rx="10"/>')
        p.append(f'<rect class="band" x="{x}" y="{y}" width="{w}" height="24" rx="10"/>'
                 f'<rect class="band" x="{x}" y="{y + 14}" width="{w}" height="10"/>')
        p.append(f'<text class="sid" x="{x + 12}" y="{y + 17}">{esc(sid)}</text>')
        p.append(f'<text class="st" x="{x + w - 12}" y="{y + 17}" text-anchor="end">{esc(st)}</text>')
        for k, line in enumerate(wrap(title, 27, 2)):
            p.append(f'<text class="name" x="{x + 12}" y="{y + 44 + k * 17}">{esc(line)}</text>')
        p.append(f'<text class="owner" x="{x + 12}" y="{y + h - 12}">{esc(owner)}</text>')
        if r:
            badges = []
            if r["q"]:
                badges.append(("bq", f"? {len(r['q'])}"))
            if r["a"]:
                badges.append(("ba", f"A {len(r['a'])}"))
            if r["r"]:
                badges.append(("br", f"R {len(r['r'])}"))
            if r["dec_total"]:
                badges.append(("bd", f"{r['dec_done']}/{r['dec_total']}"))
            bx = x + w - 10
            for bcls, txt in reversed(badges):
                bw = 8 * len(txt) + 10
                bx -= bw
                p.append(f'<g class="badge {bcls}"><rect x="{bx}" y="{y + h - 26}" width="{bw}" height="18" rx="9"/>'
                         f'<text x="{bx + bw / 2}" y="{y + h - 13}" text-anchor="middle">{esc(txt)}</text></g>')
                bx -= 4
        p.append("</g>")
    p.append("</svg>")
    return "\n".join(p)


LIGHT = """
.edge{fill:none;stroke:#7A8194;stroke-width:1.6}
.edge.dashed{stroke-dasharray:6 5;stroke:#A0A6B6}
.arrowhead{fill:#7A8194}
.elabel rect{fill:#FFFFFF;stroke:#D5D9E2}
.elabel text{font-size:11px;fill:#5E6478}
.term rect{fill:#F3F4F7;stroke:#A0A6B6;stroke-dasharray:4 3}
.term text{font-size:12px;fill:#5E6478}
.node .box{fill:#FFFFFF;stroke:#C9CEDA;stroke-width:1.2}
.node .band{fill:#EEF0F3}
.node .sid{font:600 12px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;fill:#1C2033}
.node .st{font-size:11px;font-weight:600;fill:#1C2033}
.node .name{font-size:14px;font-weight:600;fill:#1C2033}
.node .owner{font-size:12px;fill:#5E6478}
.badge rect{fill:#EEF0F3}
.badge text{font:600 11px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;fill:#1C2033}
.badge.bq rect{fill:#F6ECD6}.badge.bq text{fill:#7A5000}
.badge.ba rect{fill:#E4E3F5}.badge.ba text{fill:#3E3A9E}
.badge.br rect{fill:#F6E1DE}.badge.br text{fill:#9B2C24}
.badge.bd rect{fill:#DCEFEC}.badge.bd text{fill:#0E6E62}
.s-draft .band{fill:#E6E8EE}
.s-ready .band{fill:#FFF3C4}.s-ready .box{stroke:#C9A227;stroke-dasharray:6 3}
.s-explore .band{fill:#DCE7FB}.s-explore .box{stroke:#7DA2E8}
.s-check .band{fill:#E9E1F8}.s-check .box{stroke:#A88BE0}
.s-slice .band{fill:#F7EBCF}.s-slice .box{stroke:#D9AE4E}
.s-accept .band{fill:#F9E0CC}.s-accept .box{stroke:#E0955A}
.s-work .band{fill:#D3EEEA}.s-work .box{stroke:#4FAE9F}
.s-done .band{fill:#D7F0DC}.s-done .box{stroke:#4EA866}
.s-missing .box{stroke:#C2453A;stroke-dasharray:5 4}
"""

DARK = """
.edge{stroke:#8C93A8}.edge.dashed{stroke:#5E6478}.arrowhead{fill:#8C93A8}
.elabel rect{fill:#1C1F2B;stroke:#2E3242}.elabel text{fill:#9AA0B4}
.term rect{fill:#191C27;stroke:#4A5064}.term text{fill:#9AA0B4}
.node .box{fill:#1C1F2B;stroke:#3A3F52}.node .band{fill:#252937}
.node .sid,.node .st,.node .name{fill:#E6E8F0}.node .owner{fill:#9AA0B4}
.badge rect{fill:#252937}.badge text{fill:#E6E8F0}
.badge.bq rect{fill:#3A2E14}.badge.bq text{fill:#E2B45A}
.badge.ba rect{fill:#2A2950}.badge.ba text{fill:#A9A5F2}
.badge.br rect{fill:#3F1F1C}.badge.br text{fill:#F08A80}
.badge.bd rect{fill:#163733}.badge.bd text{fill:#5CC7B6}
.s-draft .band{fill:#2A2E3B}
.s-ready .band{fill:#3A3416}.s-ready .box{stroke:#B8962A;stroke-dasharray:6 3}
.s-explore .band{fill:#1E2E4D}.s-explore .box{stroke:#4C72B8}
.s-check .band{fill:#2E2547}.s-check .box{stroke:#7C62B5}
.s-slice .band{fill:#3A3018}.s-slice .box{stroke:#A8843A}
.s-accept .band{fill:#3D2A1C}.s-accept .box{stroke:#B57444}
.s-work .band{fill:#163733}.s-work .box{stroke:#3E8F83}
.s-done .band{fill:#18361F}.s-done .box{stroke:#3E8A52}
"""


def standalone_svg(svg: str) -> str:
    return svg.replace("<defs>", f"<style>{LIGHT}</style><defs>", 1)


def page(title: str, svg: str, roots: dict, legend_counts: dict, unplaced: list[str], meta: str, main_obj: str) -> str:
    data = json.dumps(roots, ensure_ascii=False).replace("</", "<\\/")
    legend = "".join(f'<span class="lg {STATUS_CLASS[s]}"><i></i>{esc(s)} <b>{legend_counts.get(s, 0)}</b></span>'
                     for s in STATUS_ORDER)
    warn = ""
    if unplaced:
        warn = f'<p class="warn">Нет на схеме, но есть файлы шагов: {esc(", ".join(unplaced))}. Добавьте их в блок mermaid в _map.md.</p>'
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>
:root{{--bg:#EEF0F3;--sheet:#FFFFFF;--ink:#1C2033;--muted:#5E6478;--line:#D5D9E2;--accent:#3E3A9E;color-scheme:light}}
@media (prefers-color-scheme: dark){{:root{{--bg:#14161F;--sheet:#1C1F2B;--ink:#E6E8F0;--muted:#9AA0B4;--line:#2E3242;--accent:#A9A5F2;color-scheme:dark}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
.wrap{{max-width:1500px;margin:0 auto;padding:24px 20px 48px}}
h1{{font-size:24px;margin:0 0 4px}}
.meta{{color:var(--muted);font-size:13px}}
.obj{{margin:10px 0 0;max-width:90ch}}
.legend{{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0}}
.lg{{display:inline-flex;align-items:center;gap:6px;font-size:13px;padding:3px 10px;border:1px solid var(--line);border-radius:999px;background:var(--sheet)}}
.lg i{{width:12px;height:12px;border-radius:3px;display:inline-block;background:#E6E8EE}}
.lg.s-ready i{{background:#E8C64A}}.lg.s-explore i{{background:#7DA2E8}}.lg.s-check i{{background:#A88BE0}}.lg.s-slice i{{background:#D9AE4E}}
.lg.s-accept i{{background:#E0955A}}.lg.s-work i{{background:#4FAE9F}}.lg.s-done i{{background:#4EA866}}
.hint{{font-size:13px;color:var(--muted)}}
.grid{{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:18px;align-items:start}}
.canvas{{background:var(--sheet);border:1px solid var(--line);border-radius:12px;overflow:auto;padding:6px}}
.canvas svg{{display:block;width:100%;height:auto;min-width:880px}}
.node{{cursor:pointer}}
.node:focus{{outline:none}}
.node:focus .box,.node.sel .box{{stroke:var(--accent);stroke-width:2.4}}
aside{{background:var(--sheet);border:1px solid var(--line);border-radius:12px;padding:16px 18px;position:sticky;top:12px;max-height:calc(100vh - 24px);overflow:auto}}
aside h2{{font-size:17px;margin:0 0 4px}}
aside h3{{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:14px 0 4px}}
aside ul{{margin:0;padding-left:18px}} aside li{{margin:3px 0;font-size:14px}}
aside .k{{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;color:var(--accent)}}
aside p{{margin:4px 0;font-size:14px}}
.warn{{color:#B4483C;font-size:14px}}
@media (max-width:960px){{.grid{{grid-template-columns:minmax(0,1fr)}} aside{{position:static;max-height:none}}}}
{LIGHT}
@media (prefers-color-scheme: dark){{ {DARK} }}
</style></head><body><div class="wrap">
<h1>{esc(title)}</h1>
<div class="meta">{esc(meta)}</div>
<p class="obj">{esc(main_obj)}</p>
<div class="legend">{legend}</div>
<div class="hint">Значки: <b>?</b> открытые вопросы · <b>A</b> открытые допущения · <b>R</b> риски · <b>n/m</b> декомпозиты готово / всего. Пунктир — исключения и ветки. Нажмите на шаг — справа подробности.</div>
{warn}
<div class="grid">
<div class="canvas">{svg}</div>
<aside id="info"><h2>Шаг не выбран</h2><p>Нажмите на шаг на схеме.</p></aside>
</div></div>
<script>
const R = {data};
const e = s => String(s ?? "").replace(/[&<>"]/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}}[c]));
function list(items, f) {{ return items.length ? "<ul>" + items.map(f).join("") + "</ul>" : "<p>—</p>"; }}
function show(id) {{
  document.querySelectorAll(".node").forEach(n => n.classList.toggle("sel", n.dataset.step === id));
  const r = R[id], el = document.getElementById("info");
  if (!r) {{ el.innerHTML = `<h2>${{e(id)}}</h2><p>Файла шага нет. Создайте его скиллом kb-map.</p>`; return; }}
  el.innerHTML = `<h2>${{e(id)}} ${{e(r["шаг"])}}</h2>
  <p>${{e(r["статус"])}} · владелец: ${{e(r["владелец"])}}</p><p class="k">${{e(r.path)}}</p>
  <h3>Зачем</h3><p>${{e(r["зачем"]) || "—"}}</p>
  <h3>Боль</h3>${{list(r["боль"], x => `<li>${{e(x)}}</li>`)}}
  <h3>Открытые вопросы</h3>${{list(r.q, x => `<li><span class="k">${{e(x[0])}}</span> ${{e(x[1])}} <i>(${{e(x[2])}})</i></li>`)}}
  <h3>Допущения</h3>${{list(r.a, x => `<li><span class="k">${{e(x[0])}}</span> ${{e(x[1])}} — до ${{e(x[2])}}</li>`)}}
  <h3>Риски</h3>${{list(r.r, x => `<li><span class="k">${{e(x[0])}}</span> ${{e(x[1])}} — влияние ${{e(x[2])}}</li>`)}}
  <h3>Декомпозиты</h3>${{list(r["нарезка"], x => `<li><span class="k">${{e(x[0])}}</span> ${{e(x[1])}}</li>`)}}`;
}}
document.querySelectorAll(".node").forEach(n => {{
  n.addEventListener("click", () => show(n.dataset.step));
  n.addEventListener("keydown", ev => {{ if (ev.key === "Enter" || ev.key === " ") {{ ev.preventDefault(); show(n.dataset.step); }} }});
}});
</script></body></html>
"""
