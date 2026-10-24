"""What agents see: compact text views of the IR (no LDraw syntax, no geometry).

Unit digest: for each section to decide, a header (id, name, size, where it is used, path from
the root, current description) and one line per item:
    i12 Technic Beam 7 (black) @40,-8,120
    i13 > S7 "8466 - wheel.ldr" [2 items] "Wheel assembly: ..." @60,0,-40
    --- step
Positions are LDU in the section's own frame (x right, y DOWN, z towards viewer; 20 LDU = 1 stud,
8 LDU = 1 plate, 24 LDU = 1 brick). Tree view: the hierarchy with descriptions (bottom-up refine).
"""

import sqlite3
from functools import cache

from . import mpd

PARTS_PER_UNIT = 300  # pack small sections together up to this many items per LLM call
SECTIONS_PER_UNIT = 30


@cache
def bboxes():
    """Part bounding boxes (local LDU): normalized name -> (min xyz, max xyz)."""
    conn = sqlite3.connect(mpd.apd.DB_FILE.as_uri() + "?mode=ro", uri=True)
    try:
        return {mpd.normalized(a): ((x0, y0, z0), (x1, y1, z1)) for a, x0, y0, z0, x1, y1, z1 in
                conn.execute("SELECT alias, min_x, min_y, min_z, max_x, max_y, max_z FROM PART_BBOXES")}
    finally:
        conn.close()


def transform_box(box, m):
    """Axis-aligned box of a local box placed with type-1 numbers m = [x y z a b c d e f g h i]."""
    (x0, y0, z0), (x1, y1, z1) = box
    xs = []
    for cx in (x0, x1):
        for cy in (y0, y1):
            for cz in (z0, z1):
                xs.append((m[0] + m[3] * cx + m[4] * cy + m[5] * cz,
                           m[1] + m[6] * cx + m[7] * cy + m[8] * cz,
                           m[2] + m[9] * cx + m[10] * cy + m[11] * cz))
    return tuple(map(min, zip(*xs))), tuple(map(max, zip(*xs)))


def section_box(ir, s, memo=None):
    """Bounding box of a model/flex section in its own frame (None if nothing is known)."""
    memo = {} if memo is None else memo
    if s["id"] in memo:
        return memo[s["id"]]
    memo[s["id"]] = None  # cycle guard
    names, boxes = mpd.section_by_name(ir), []
    lines = [l for e in s.get("body", []) for l in (e["lines"] if isinstance(e, dict) else [e])]
    for line in lines:
        t = mpd.type1(line)
        if not t:
            continue
        ref = mpd.normalized(t[2])
        child = names.get(ref)
        box = section_box(ir, child, memo) if child and "body" in child else bboxes().get(ref)
        if box:
            boxes.append(transform_box(box, t[1]))
    box = (tuple(map(min, zip(*(b[0] for b in boxes)))), tuple(map(max, zip(*(b[1] for b in boxes))))) \
        if boxes else None
    memo[s["id"]] = box
    return box


def size_text(box):
    if not box:
        return ""
    (x0, y0, z0), (x1, y1, z1) = box
    return f", size {(x1 - x0) / 20:.0f}x{(z1 - z0) / 20:.0f} studs x {(y1 - y0) / 24:.1f} bricks high"


def parents(ir):
    """Section id -> Counter-like dict of parent id -> number of references."""
    out = {}
    for s in ir["sections"].values():
        if s["kind"] == "model":
            for child in mpd.children(ir, s):
                out.setdefault(child["id"], {}).setdefault(s["id"], 0)
                out[child["id"]][s["id"]] += 1
    return out


def path_to(ir, sid, ups):
    """'S1 "main.ldr" > S4 "chassis.ldr"': the first chain of parents up to the root."""
    chain, seen = [], set()
    while sid in ups and sid not in seen:
        seen.add(sid)
        sid = next(iter(ups[sid]))
        chain.append(sid)
    return " > ".join(f'{p} "{ir["sections"][p]["name"]}"' for p in reversed(chain))


def item_line(ir, k, item, names):
    line = mpd.ref_line(item)
    colour, m, ref = mpd.type1(line)
    pos = f"@{m[0]:.0f},{m[1]:.0f},{m[2]:.0f}"
    child = names.get(mpd.normalized(ref))
    if child and child["kind"] in ("model", "flex"):
        n = len(mpd.items(child)) if child["kind"] == "model" else 0
        count = f" [{n} items]" if n else ""
        return f'i{k} > {child["id"]} "{child["name"]}"{count} "{child.get("desc") or ""}" {pos}'
    comment = next((l[5:] for l in item["lines"] if l.startswith("0 // ")), None)
    if comment is None:  # unknown part: show its file name and colour
        comment = f"{ref} ({mpd.apd.COLOURS_DESCRIPTIONS.get(colour, colour)})"
    return f"i{k} {comment} {pos}"


def section_view(ir, s, ups, names):
    n = len(mpd.items(s))
    used = ups.get(s["id"], {})
    where = ", ".join(f"{c}x in {p}" if c > 1 else f"in {p}" for p, c in used.items())
    task = "decide: leaf, or split one level" if s["state"] == "open" else "describe only (do not split)"
    if s.get("orphan"):
        task += "; unreferenced section, not part of the main model"
    if s["state"] == "open" and s.get("passes"):
        task += ("; already split before (its children are items here): split off more assemblies "
                 "from the remaining parts only if there are any, else leaf")
    out = [f'## {s["id"]} "{s["name"]}" -- {task}',
           f'{n} items{", used " + where if where else ""}{size_text(section_box(ir, s))}']
    if path := path_to(ir, s["id"], ups):
        out.append(f"path: {path}")
    out.append(f'current description: {s.get("desc") or "(none)"}')
    k = 0
    for e in s["body"]:
        if isinstance(e, dict):
            out.append(item_line(ir, k, e, names))
            k += 1
        elif mpd.STEP.match(e) and out[-1] != "--- step" and k:
            out.append("--- step")
    if out[-1] == "--- step":
        out.pop()
    return out


def subject(ir):
    """One line naming the model: file name, root description/title, keywords and theme."""
    root = ir["sections"][ir["root"]]
    meta = [l.split(None, 1)[1] for l in root.get("head", []) if l.startswith(("0 !KEYWORDS", "0 !THEME"))]
    return f'Model file: {ir["name"]}; root title: {root.get("desc") or "(none)"}' + \
        (f'; {"; ".join(meta)}' if meta else "")


def unit_digest(ir, section_ids):
    ups, names = parents(ir), mpd.section_by_name(ir)
    out = [subject(ir), ""]
    for sid in section_ids:
        out += section_view(ir, ir["sections"][sid], ups, names) + [""]
    return "\n".join(out)


def tree_digest(ir, section_ids):
    """Indented hierarchy with descriptions; asks to rewrite section_ids (parents)."""
    out, seen = [subject(ir), ""], set()

    def visit(s, depth, uses):
        n = len(mpd.items(s)) if s["kind"] == "model" else 0
        times = f" x{uses}" if uses > 1 else ""
        again = " (see above)" if s["id"] in seen else ""
        out.append(f'{"  " * depth}{s["id"]} "{s["name"]}"{times} [{n} items] {s.get("desc") or ""}{again}')
        if again or s["kind"] != "model":
            return
        seen.add(s["id"])
        counts = {}
        for child in mpd.children(ir, s):
            counts[child["id"]] = counts.get(child["id"], 0) + 1
        for cid, uses in counts.items():
            visit(ir["sections"][cid], depth + 1, uses)

    visit(ir["sections"][ir["root"]], 0, 1)
    out += ["", "Rewrite the descriptions of: " + ", ".join(section_ids)]
    return "\n".join(out)


def refine_ids(ir):
    """Sections whose description is rewritten bottom-up: model sections that have children."""
    return [sid for sid in mpd.output_order(ir)
            if ir["sections"][sid]["kind"] == "model" and mpd.children(ir, ir["sections"][sid])]


def pack_units(ir, section_ids):
    """Group sections (in reading order) into units of at most PARTS_PER_UNIT items."""
    units, cur, size = [], [], 0
    for sid in section_ids:
        n = len(mpd.items(ir["sections"][sid]))
        if cur and (size + n > PARTS_PER_UNIT or len(cur) >= SECTIONS_PER_UNIT):
            units.append(cur)
            cur, size = [], 0
        cur.append(sid)
        size += n
    return units + ([cur] if cur else [])
