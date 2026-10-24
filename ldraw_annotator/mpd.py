"""Streaming LDraw MPD/LDR reader and writer, and the model IR.

The IR is a plain JSON-serializable dict, saved once per round (resume) and readable when
debugging. Only *model* sections are parsed into items; embedded parts and generated geometry
are kept as line spans of the source file and copied verbatim when writing, so a 50 MB file
costs about as much as its few thousand part references.

Section kinds:
  model  part/submodel references + meta lines: what agents structure into a hierarchy
  flex   a flexible part: an LDCad-generated path/spring section, or an LSynth block we moved out
  part   opaque geometry: embedded .dat parts, generated meshes, anything with type 2-5 lines

IR shape (see load()):
  {"source", "name", "bom", "eol", "prefix", "author", "license", "root", "next_id",
   "preamble": [start, end], "sections": {id: section}, "order": [ids in source order]}
  model section: {"id", "name", "kind": "model", "desc", "has_title", "head": [lines],
                  "keywords": [], "body": [entry], "state": "open|leaf|split", "orphan": bool}
  entry: a passthrough line (str) or an item {"lines": [...], "ref": normalized reference}
  flex section: {"id", "name", "kind": "flex", "desc", "keywords", "span" | "head"+"body"}
  part section: {"id", "name", "kind": "part", "span": [start, end]}
"""

import importlib.util
import io
import os
import re
from collections import Counter
from pathlib import Path
from tempfile import NamedTemporaryFile

from . import flex

ROOT = Path(__file__).resolve().parents[1]

# The part-description logic lives in scripts/add_parts_descriptions.py (mandated by the task);
# load it as a module so the pipeline and the standalone script share one implementation.
_spec = importlib.util.spec_from_file_location(
    "add_parts_descriptions", ROOT / "scripts" / "add_parts_descriptions.py")
apd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(apd)

normalized = apd.normalized

# Sections with fewer items are leaves without asking an agent: splitting them could only
# produce single-item wrappers. Orphan (unreferenced) sections are only described.
MIN_SPLIT_ITEMS = 4

# Lines that belong to the part right below them and must move with it.
ATTACHED = re.compile(r"0 (//|BFC INVERTNEXT|MLCAD BTG|!LDCAD GROUP_NXT|!TEXMAP NEXT)")
STEP = re.compile(r"0 (STEP|ROTSTEP)\b")
PART_ORG = re.compile(r"part|subpart|primitive|shortcut", re.I)
# First words that make a type-0 line a meta command or header, not a description/title.
NOT_TITLE = {"NAME:", "AUTHOR:", "BFC", "FILE", "NOFILE", "STEP", "ROTSTEP", "WRITE", "PRINT",
             "SAVE", "CLEAR", "PAUSE", "GHOST", "MLCAD", "LPUB", "SYNTH", "SYNTHESIZED",
             "ROTATION", "BUFEXCHG", "GROUP"}


# ---------------------------------------------------------------- reading


def read_lines(path):
    """Return (lines keeping their own line endings, has_bom). Never fails on bad bytes."""
    data = Path(path).read_bytes()
    bom = data.startswith(b"\xef\xbb\xbf")
    text = data[3 if bom else 0:].decode("utf-8", "surrogateescape")
    return list(io.StringIO(text, newline="")), bom


def strip(line):
    return line.rstrip("\r\n")


def words(line, n=-1):
    return line.split(None, n) if n >= 0 else line.split()


def is_title(line):
    """True for a plain type-0 text line usable as a description (not a comment or command)."""
    w = words(line, 2)
    if len(w) < 2 or w[0] != "0":
        return False
    text = line.split(None, 1)[1].strip()
    return not text.startswith(("//", "/*", "!", "0 ")) and w[1].upper() not in NOT_TITLE


def type1(line):
    """Parse a type-1 line: (colour, [x, y, z, a..i], reference) or None."""
    w = line.split(None, 14)
    if len(w) < 15 or w[0] != "1":
        return None
    try:
        return w[1], [float(v) for v in w[2:14]], w[14].strip()
    except ValueError:
        return None


def scan(lines, default_name):
    """Split source lines into raw sections: [{"name", "start", "end"}], plus preamble end.

    A file without "0 FILE" is one section named after the input file.
    """
    sections, preamble_end = [], len(lines)
    for i, line in enumerate(lines):
        w = words(line, 2)
        if w[:2] == ["0", "FILE"] and len(w) == 3:
            if sections:
                sections[-1]["end"] = i
            else:
                preamble_end = i
            sections.append({"name": strip(w[2]).strip(), "start": i, "end": len(lines)})
    if not sections:
        return [{"name": default_name, "start": 0, "end": len(lines), "implicit": True}], 0
    # Content before the first FILE that has geometry is really an implicit root model.
    if any(words(l, 1)[:1] in (["1"], ["2"], ["3"], ["4"], ["5"]) for l in lines[:preamble_end]):
        sections.insert(0, {"name": default_name, "start": 0, "end": preamble_end, "implicit": True})
        preamble_end = 0
    return sections, preamble_end


def classify(lines, sec):
    """Return "model", "flex" or "part" for a raw section, plus its references."""
    if normalized(sec["name"]).endswith(".dat"):
        kind = "part"
    else:
        kind = None
    geo = generated = content = flexible = False
    refs = []
    for line in lines[sec["start"]:sec["end"]]:
        w = words(line, 3)
        if not w:
            continue
        if w[0] == "1":
            t = type1(line)
            if t:
                refs.append(normalized(t[2]))
        elif w[0] in ("2", "3", "4", "5"):
            geo = True
        elif w[:2] == ["0", "!LDRAW_ORG"] and len(w) > 2 and PART_ORG.search(w[2]):
            kind = kind or "part"
        elif w[:2] == ["0", "!KEYWORDS"] and len(w) > 2 and w[2].lower().startswith("flexible"):
            flexible = True  # our flex sections (and LDCad's) carry "0 !KEYWORDS flexible, <kind>"
        elif w[:2] == ["0", "!LDCAD"] and len(w) > 2:
            generated |= w[2] == "GENERATED"
            content |= w[2] == "CONTENT"
    if kind is None:
        if (generated and content) or flexible:
            kind = "flex"
        elif generated or geo:
            kind = "part"
        else:
            kind = "model"
    return kind, refs


def split_head(lines):
    """Split a model section's lines (after "0 FILE") into title, header lines and body lines.

    The header runs until the first part, step, part comment or attached meta line.
    """
    title, i = None, 0
    if lines and is_title(lines[0]):
        title, i = lines[0].split(None, 1)[1].strip(), 1
    j = i
    while j < len(lines):
        w = words(lines[j], 1)
        if w and (w[0] in ("1", "2", "3", "4", "5") or STEP.match(lines[j]) or ATTACHED.match(lines[j])
                  or lines[j].startswith(("0 SYNTH", "0 WRITE SYNTH", "0 !TEXMAP", "0 LPUB PLI"))):
            break
        j += 1
    return title, lines[i:j], lines[j:]


def itemize(body):
    """Turn body lines into entries: passthrough lines and items (a part with its attached lines).

    A "0 !TEXMAP START ... END" block is one atomic item.
    """
    entries, pending, i = [], [], 0
    while i < len(body):
        line = body[i]
        if line.startswith("0 !TEXMAP START"):
            j = i
            while j < len(body) and not body[j].startswith("0 !TEXMAP END"):
                j += 1
            block = body[i:j + 1]
            refs = [type1(l)[2] for l in block if type1(l)]
            entries.append({"lines": pending + block, "ref": normalized(refs[0]) if refs else ""})
            pending, i = [], j + 1
            continue
        t = type1(line)
        if t:
            entries.append({"lines": pending + [line], "ref": normalized(t[2])})
            pending = []
        elif ATTACHED.match(line):
            pending.append(line)
        else:
            entries += pending
            pending = []
            entries.append(line)
        i += 1
    return entries + pending


def common_prefix(names, stem):
    """Prefix for new section names: the "<set> - " shared by existing names, else from the file."""
    heads = Counter(n.split(" - ", 1)[0] + " - " for n in names if " - " in n)
    if heads:
        head, count = heads.most_common(1)[0]
        if count * 2 >= len(names):
            return head
    m = re.match(r"\d+", stem)
    return (m.group(0) if m else stem) + " - "


def load(path):
    """Read a model file into the IR: classify sections, isolate flexible parts, add part comments."""
    path = Path(path)
    lines, bom = read_lines(path)
    eols = Counter(l[len(strip(l)):] for l in lines if l.endswith(("\n", "\r")))
    raw, preamble_end = scan(lines, path.name if path.suffix.lower() != ".mpd" else path.stem + ".ldr")

    apd.cache_descriptions_db()
    sections, order, by_name, refs_of = {}, [], {}, {}
    for n, sec in enumerate(raw, 1):
        sid = f"S{n}"
        kind, refs = classify(lines, sec)
        s = {"id": sid, "name": sec["name"], "kind": kind}
        if kind == "part" or kind == "flex":
            s["span"] = [sec["start"], sec["end"]]
        sections[sid], refs_of[sid] = s, refs
        order.append(sid)
        by_name.setdefault(normalized(sec["name"]), sid)

    # The hierarchy is what the root reaches through model sections. Other model sections are
    # orphans (kept and described only), unless they're only used from inside embedded parts.
    def reach(starts):
        seen, stack = set(starts), list(starts)
        while stack:
            for ref in refs_of[stack.pop()]:
                sid = by_name.get(ref)
                if sid and sid not in seen and sections[sid]["kind"] == "model":
                    seen.add(sid)
                    stack.append(sid)
        return seen

    root = order[0]
    models = [sid for sid in order if sections[sid]["kind"] == "model"]
    used_by_models = {by_name.get(r) for sid in models for r in refs_of[sid]}
    main = reach([root])
    orphans = reach([sid for sid in models if sid not in main and sid not in used_by_models])
    for n, sid in enumerate(order):
        s = sections[sid]
        if s["kind"] == "model" and sid not in main:
            if sid in orphans:
                s["orphan"] = True
            else:  # only used from inside embedded parts: a geometry helper
                s["kind"], s["span"] = "part", [raw[n]["start"], raw[n]["end"]]

    # Embedded parts describe themselves with their title line (used for "0 //" comments).
    extra = {}
    for n, sid in enumerate(order):
        s = sections[sid]
        if s["kind"] == "part":
            a, b = s["span"]
            title = strip(lines[a + 1]) if a + 1 < b and not raw[n].get("implicit") else ""
            if is_title(title):
                extra[normalized(s["name"])] = title.split(None, 1)[1].strip()

    model_names = [sections[sid]["name"] for sid in order if sections[sid]["kind"] != "part"]
    ir = {"source": str(path.resolve()), "name": path.name, "bom": bom,
          "eol": eols.most_common(1)[0][0] if eols else "\n",
          "prefix": common_prefix(model_names, path.stem),
          "root": root, "next_id": len(order) + 1, "preamble": [0, preamble_end],
          "sections": sections, "order": order, "author": None, "license": None}

    for n, sid in enumerate(list(order)):  # snapshot: flex.extract() appends new sections
        s = sections[sid]
        if s["kind"] == "flex":
            flex.describe_ldcad(s, [strip(l) for l in lines[s["span"][0]:s["span"][1]]])
            continue
        if s["kind"] != "model":
            continue
        sec = raw[n]
        body_from = sec["start"] if sec.get("implicit") else sec["start"] + 1
        text = [strip(l) for l in lines[body_from:sec["end"]]]
        title, head, body = split_head(text)
        if sid == root:  # new sections copy the root's attribution
            for line in head:
                w = words(line, 1)
                if len(w) == 2 and w[1].startswith("Author:"):
                    ir["author"] = ir["author"] or line
                elif len(w) == 2 and w[1].startswith("!LICENSE"):
                    ir["license"] = ir["license"] or line
        body = flex.extract(ir, s, body)  # LSynth blocks / segment runs -> own flex sections
        body = apd.annotate_lines(body, extra)
        s.setdefault("orphan", False)
        s.update(desc=title, has_title=title is not None, head=head, keywords=[], body=itemize(body),
                 todo=True)  # every existing model section gets described in round 1
        s["state"] = "open" if not s["orphan"] and len(items(s)) >= MIN_SPLIT_ITEMS else "leaf"
    return ir


# ---------------------------------------------------------------- queries


def items(section):
    """The items (part/submodel references) of a model section, in order."""
    return [e for e in section["body"] if isinstance(e, dict)]


def ref_line(item):
    """The (last) type-1 line of an item."""
    return next(l for l in reversed(item["lines"]) if type1(l))


def section_by_name(ir):
    return {normalized(s["name"]): s for s in ir["sections"].values()}


def children(ir, section):
    """Model/flex sections directly referenced by a model section (in order, with repeats)."""
    names = section_by_name(ir)
    return [names[i["ref"]] for i in items(section)
            if i["ref"] in names and names[i["ref"]]["kind"] in ("model", "flex")]


def new_id(ir):
    sid = f"S{ir['next_id']}"
    ir["next_id"] += 1
    return sid


def unique_name(ir, slug):
    """A new section name "<prefix><slug>.ldr" not used yet (case-insensitive)."""
    slug = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")[:40] or "group"
    taken = section_by_name(ir)
    name, n = f"{ir['prefix']}{slug}.ldr", 2
    while normalized(name) in taken:
        name, n = f"{ir['prefix']}{slug}-{n}.ldr", n + 1
    return name


def new_section(ir, name, kind="model", desc=None, body=None, keywords=None):
    """Create a section with a minimal header copied from the root (Name/Author/License)."""
    head = [f"0 Name: {name}"] + [l for l in (ir["author"], ir["license"]) if l]
    s = {"id": new_id(ir), "name": name, "kind": kind, "desc": desc, "has_title": True,
         "head": head, "keywords": keywords or [], "body": body or [], "state": "open",
         "orphan": False, "new": True}
    ir["sections"][s["id"]] = s
    ir["order"].append(s["id"])
    return s


def identity_ref(name):
    """Reference to a submodel in its parent's frame: colour 16 (inherit), identity matrix."""
    return {"lines": [f"1 16 0 0 0 1 0 0 0 1 0 0 0 1 {name}"], "ref": normalized(name)}


# ---------------------------------------------------------------- writing


def output_order(ir):
    """Model sections in DFS pre-order from the root, then orphans, flex sections, embedded parts."""
    seen, out = set(), []

    def visit(s):
        if s["id"] in seen or s["kind"] != "model":
            return
        seen.add(s["id"])
        out.append(s["id"])
        for child in children(ir, s):
            visit(child)

    visit(ir["sections"][ir["root"]])
    for sid in ir["order"]:
        if ir["sections"][sid]["kind"] == "model":
            visit(ir["sections"][sid])
    for kind in ("flex", "part"):
        out += [sid for sid in ir["order"] if ir["sections"][sid]["kind"] == kind]
    return out


def header_lines(s):
    """FILE line, description, original header lines and our keywords line."""
    head = list(s.get("head", []))
    while head and not head[-1].strip():
        head.pop()
    if s.get("keywords"):
        head.append("0 !KEYWORDS " + ", ".join(s["keywords"]))
    desc = s.get("desc") or Path(s["name"]).stem
    return [f"0 FILE {s['name']}", f"0 {desc}"] + head


def render(ir, s, src):
    """Lines of one section; generated lines without endings, raw lines with their own."""
    if s["kind"] == "part":
        return src[s["span"][0]:s["span"][1]]
    if s["kind"] == "flex" and "span" in s:
        raw = src[s["span"][0]:s["span"][1]]
        rest = raw[2:] if s.get("has_title") else raw[1:]
        head = [f"0 {s['desc']}"] + (["0 !KEYWORDS " + ", ".join(s["keywords"])] if s["keywords"] else [])
        return [raw[0]] + head + rest
    body = [l for e in s["body"] for l in (e["lines"] if isinstance(e, dict) else [e])]
    return header_lines(s) + body


def write(ir, out_path):
    """Write the annotated, self-contained MPD atomically (temp file + rename)."""
    src, _ = read_lines(ir["source"])
    eol, out = ir["eol"], []
    out += src[ir["preamble"][0]:ir["preamble"][1]] if any(l.strip() for l in src[:ir["preamble"][1]]) else []
    first_model = True
    for sid in output_order(ir):
        s = ir["sections"][sid]
        lines = [l if l.endswith(("\n", "\r")) else l + eol for l in render(ir, s, src)]
        if s["kind"] == "model":
            while lines and not lines[-1].strip():
                lines.pop()
            # Every submodel starts its own build step: "0 STEP" right before each later "0 FILE".
            if not first_model:
                while out and not out[-1].strip():
                    out.pop()
                if not (out and STEP.match(out[-1])):
                    out.append("0 STEP" + eol)
            first_model = False
        out += lines
    data = ("﻿" if ir["bom"] else "") + "".join(out)
    atomic_write(out_path, data.encode("utf-8", "surrogateescape"))


def atomic_write(path, data):
    """Write bytes or text so readers never see a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode("utf-8", "surrogateescape")
    with NamedTemporaryFile(dir=path.parent, delete=False) as f:
        f.write(data)
    os.replace(f.name, path)
