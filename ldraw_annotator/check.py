"""Verify an annotated output against its input (docs/output-contract.md).

Checks:
  lossless     input and output flatten to the same multiset of (part, effective colour,
               world matrix): no part lost, duplicated or moved; orphans compared too
  parts        embedded part sections are byte-identical
  flex         flexible-part sections keep their content (only the header may change)
  contract     description after every model/flex "0 FILE"; "0 //" above every known part;
               "0 STEP" before every later model "0 FILE"; model sections in DFS pre-order
  references   no cycles; unknown references are reported (warnings)
  pyldraw3     the output parses without errors and every section's description is ours
"""

from collections import Counter

from . import mpd


def sections(path):
    """name (normalized) -> {"name", "kind", "lines"} for a file, plus names in file order."""
    lines, _ = mpd.read_lines(path)
    raw, _ = mpd.scan(lines, path.name if hasattr(path, "name") else str(path))
    out, order = {}, []
    for sec in raw:
        kind, _ = mpd.classify(lines, sec)
        key = mpd.normalized(sec["name"])
        if key not in out:
            out[key] = {"name": sec["name"], "kind": kind,
                        "lines": [mpd.strip(l) for l in lines[sec["start"]:sec["end"]]]}
            order.append(key)
    return out, order


def flatten(secs, root):
    """Counter of (reference, effective colour, rounded world matrix) over physical leaves."""
    leaves, errors = Counter(), []

    def visit(key, m, colour, stack):
        if key in stack:
            errors.append("cycle: " + " > ".join(stack + [key]))
            return
        for line in secs[key]["lines"]:
            t = mpd.type1(line)
            if not t:
                continue
            c, local, ref = t
            c = colour if c == "16" else c
            world = compose(m, local)
            child = mpd.normalized(ref)
            if child in secs and secs[child]["kind"] in ("model", "flex"):
                visit(child, world, c, stack + [key])
            else:
                leaves[(child, c, tuple(round(v, 3) + 0.0 for v in world))] += 1

    visit(root, [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1], "16", [])
    return leaves, errors


def compose(p, l):
    """World placement of local placement l inside parent placement p (type-1 number order)."""
    (px, py, pz, a, b, c, d, e, f, g, h, i), (lx, ly, lz, *r) = p, l
    rot = [a * r[0] + b * r[3] + c * r[6], a * r[1] + b * r[4] + c * r[7], a * r[2] + b * r[5] + c * r[8],
           d * r[0] + e * r[3] + f * r[6], d * r[1] + e * r[4] + f * r[7], d * r[2] + e * r[5] + f * r[8],
           g * r[0] + h * r[3] + i * r[6], g * r[1] + h * r[4] + i * r[7], g * r[2] + h * r[5] + i * r[8]]
    return [px + a * lx + b * ly + c * lz, py + d * lx + e * ly + f * lz, pz + g * lx + h * ly + i * lz] + rot


def roots(secs, order):
    """The root plus every model section not referenced by any section (orphans)."""
    referenced = {mpd.normalized(t[2]) for s in secs.values() for l in s["lines"] if (t := mpd.type1(l))}
    return [order[0]] + [k for k in order[1:] if secs[k]["kind"] == "model" and k not in referenced]


def check(input_path, output_path, pyldraw=True):
    """Return a report dict; report["ok"] is False if any error was found."""
    from pathlib import Path
    input_path, output_path = Path(input_path), Path(output_path)
    src, src_order = sections(input_path)
    out, out_order = sections(output_path)
    errors, warnings = [], []

    # lossless: the root (by position) and every orphan (by name)
    a, ea = flatten(src, src_order[0])
    b, eb = flatten(out, out_order[0])
    errors += ea + eb
    for key in roots(src, src_order)[1:]:
        if key not in out:
            errors.append(f"orphan section missing: {src[key]['name']}")
            continue
        a2, _ = flatten(src, key)
        b2, _ = flatten(out, key)
        a, b = a + a2, b + b2
    if a != b:
        lost, extra = a - b, b - a
        errors.append(f"not lossless: {sum(lost.values())} placements lost, {sum(extra.values())} new; "
                      f"e.g. lost {list(lost)[:2]}, new {list(extra)[:2]}")

    for key, s in src.items():
        o = out.get(key)
        if s["kind"] == "part" and (o is None or o["lines"] != s["lines"]):
            errors.append(f"embedded part changed or missing: {s['name']}")
        if s["kind"] == "flex" and (o is None or body(o["lines"]) != body(s["lines"])):
            errors.append(f"flexible part content changed or missing: {s['name']}")

    errors += contract(out, out_order)
    unknown = sorted({mpd.normalized(t[2]) for s in out.values() if s["kind"] == "model" for l in s["lines"]
                      if (t := mpd.type1(l)) and mpd.normalized(t[2]) not in out
                      and mpd.normalized(t[2]) not in mpd.apd.PARTS_DESCRIPTIONS})
    if unknown:
        warnings.append(f"references not in the file nor the parts DB: {unknown[:10]}")
    if pyldraw:
        errors += pyldraw_errors(output_path, out, input_path)
    return {"ok": not errors, "input": str(input_path), "output": str(output_path),
            "placements": sum(b.values()), "sections": len(out_order),
            "model_sections": sum(1 for s in out.values() if s["kind"] == "model"),
            "errors": errors, "warnings": warnings}


def body(lines):
    """Section lines without the FILE line, title/description and our flexible keywords line."""
    rest = lines[1:]
    if rest and mpd.is_title(rest[0]):
        rest = rest[1:]
    return [l for l in rest if not l.startswith("0 !KEYWORDS flexible")]


def contract(out, order):
    mpd.apd.cache_descriptions_db()
    errors, models = [], [k for k in order if out[k]["kind"] == "model"]
    for k in order:
        s = out[k]
        if s["kind"] in ("model", "flex") and not (len(s["lines"]) > 1 and mpd.is_title(s["lines"][1])):
            errors.append(f"no description line after 0 FILE: {s['name']}")
    for n, k in enumerate(models):
        lines = out[k]["lines"]
        for j, line in enumerate(lines):
            t = mpd.type1(line)
            if t and mpd.normalized(t[2]) not in out and mpd.normalized(t[2]) in mpd.apd.PARTS_DESCRIPTIONS:
                above = j - 1
                while above > 0 and lines[above].startswith("0 BFC INVERTNEXT"):
                    above -= 1
                if not lines[above].startswith("0 // "):
                    errors.append(f"part without '0 //' description: {out[k]['name']} line {j + 1}")
                    break
    # "0 STEP" right before every later model FILE (it is the last line of the previous section)
    for prev, k in zip(order, order[1:]):
        if out[k]["kind"] == "model":
            tail = [l for l in out[prev]["lines"] if l.strip()]
            if not (tail and mpd.STEP.match(tail[-1])):
                errors.append(f"missing '0 STEP' before 0 FILE {out[k]['name']}")
    expected = dfs(out, order)
    if models != expected:
        errors.append("model sections are not in DFS pre-order (parent first, then its children)")
    return errors


def dfs(out, order):
    seen, result = set(), []

    def visit(k):
        if k in seen or out[k]["kind"] != "model":
            return
        seen.add(k)
        result.append(k)
        for line in out[k]["lines"]:
            if (t := mpd.type1(line)) and mpd.normalized(t[2]) in out:
                visit(mpd.normalized(t[2]))

    for k in order:
        visit(k)
    return result


def pyldraw_errors(path, out, input_path):
    """Parse with pyldraw3 (what consumers use) and compare the descriptions it reports.

    Errors the input already had (e.g. legacy dithered colours) are not ours and are ignored.
    """
    try:
        from ldraw import parse_model_result
    except ImportError:
        return []

    def parse(p):
        result = parse_model_result("".join(mpd.read_lines(p)[0]), name=p.name)
        return result, {(str(d.code), d.offending_value) for d in result.diagnostics if d.severity == "error"}

    result, found = parse(path)
    if found:
        found -= parse(input_path)[1]
    errors = [f"pyldraw3: {code} {value!r}" for code, value in sorted(found, key=str)][:5]
    if result.model is None:
        return errors or ["pyldraw3: no model parsed"]
    models = {mpd.normalized(result.model.name): result.model}
    models.update({mpd.normalized(k): v for k, v in result.model.submodels.items()})
    for key, s in out.items():
        m = models.get(key)
        if s["kind"] == "model" and m is not None:
            ours = s["lines"][1].split(None, 1)[1].strip() if len(s["lines"]) > 1 else ""
            if (m.description or "").strip() != ours:
                errors.append(f"pyldraw3 reads another description for {s['name']}: {m.description!r}")
    return errors
