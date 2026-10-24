"""Agent plans: JSON schemas, validation and deterministic application to the IR.

A split plan decides, for every section of a unit, its description and either
  - "new": []      -> leaf: the section is one semantic unit, recursion stops here, or
  - "new": [...]   -> split one level: groups of item ids become new child submodels,
plus optional "groups": comment lines ("0 /* ... */") above contiguous runs of items.
A refine plan only rewrites descriptions of sections that have children (bottom-up).

Item ids are the "iN" numbers of the digest, written as ranges: "0-7,12".
"""

import re

from . import mpd

# Machine-readable function tags (output contract, "0 !KEYWORDS"). Keep in sync with the docs.
KEYWORDS = ["steering", "suspension", "gearbox", "differential", "drivetrain", "engine", "piston",
            "turntable", "winch", "crane", "boom", "hinge", "door", "wheel", "landing-gear",
            "propeller", "minifig", "chassis", "frame", "body", "interior", "base"]
MAX_DESCRIPTION = 300
REOFFER_PARTS = 60  # a split section keeping this many loose parts is offered again...
MAX_PASSES = 3  # ...up to this many decisions in total

_keywords = {"type": "array", "items": {"type": "string", "enum": KEYWORDS}}
SPLIT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["sections"],
    "properties": {"sections": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "description", "new"],
        "properties": {
            "id": {"type": "string"},
            "description": {"type": "string"},
            "keywords": _keywords,
            "rename": {"type": "string"},
            "new": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["name", "items", "description"],
                "properties": {"name": {"type": "string"}, "items": {"type": "string"},
                               "description": {"type": "string"}, "keywords": _keywords}}},
            "groups": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["items", "comment"],
                "properties": {"items": {"type": "string"}, "comment": {"type": "string"}}}}}}}}}
REFINE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["sections"],
    "properties": {"sections": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "description"],
        "properties": {"id": {"type": "string"}, "description": {"type": "string"}}}}}}


def can_split(section):
    return section["state"] == "open"


def parse_items(text, count):
    """'0-3,7' -> [0, 1, 2, 3, 7]; raises ValueError on bad syntax or out-of-range ids."""
    ids = []
    for part in str(text).replace(" ", "").replace("i", "").split(","):
        if not part:
            continue
        a, _, b = part.partition("-")
        lo, hi = int(a), int(b or a)
        if lo > hi or lo < 0 or hi >= count:
            raise ValueError(f"item range {part!r} outside 0-{count - 1}")
        ids += range(lo, hi + 1)
    if not ids:
        raise ValueError("empty item list")
    return ids


def description_error(text):
    """None if text is a valid one-line description (see docs/output-contract.md)."""
    if not isinstance(text, str) or not text.strip():
        return "description is empty"
    if len(text) > MAX_DESCRIPTION or any(ord(c) < 32 for c in text):
        return f"description must be one line of at most {MAX_DESCRIPTION} characters"
    if not mpd.is_title("0 " + text.strip()):
        return "description must be plain text (not starting with //, /*, !, Name:, or an uppercase meta command like STEP)"
    return None


def format_items(ids):
    """[0, 1, 2, 3, 7] -> '0-3,7'."""
    ids, out = sorted(ids), []
    for k in ids:
        if out and out[-1][1] == k - 1:
            out[-1][1] = k
        else:
            out.append([k, k])
    return ",".join(f"{a}-{b}" if a != b else str(a) for a, b in out)


def repair_split(ir, section_ids, plan):
    """Check a split answer, repairing harmless problems in place; returns (errors, notes).

    Repaired without another LLM call (each one noted):
      - a new submodel with fewer than 2 items is dropped, so its item stays in the section;
      - an item listed in two new submodels stays in the first one;
      - a single child holding every item, or a split of a describe-only section, becomes a leaf;
      - an invalid group comment is dropped; unknown keywords are dropped.
    Errors (the answer is asked for again): unknown or missing sections, unreadable item ranges,
    empty names, invalid descriptions.
    """
    errors, notes, seen = [], [], set()
    for sp in plan.get("sections", []):
        sid = sp.get("id")
        if sid not in section_ids or sid in seen:
            errors.append(f"{sid}: not a section of this task (or listed twice)")
            continue
        seen.add(sid)
        s = ir["sections"][sid]
        n = len(mpd.items(s))
        if err := description_error(sp.get("description")):
            errors.append(f"{sid}: {err}")
        sp["keywords"] = [k for k in sp.get("keywords") or [] if k in KEYWORDS]
        if sp.get("rename") and not (s.get("rename") and str(sp["rename"]).strip()):
            notes.append(f"{sid}: only sections with a meaningless name are renamed; name kept")
            sp.pop("rename")
        new = sp.get("new") or []
        if new and not can_split(s):
            notes.append(f"{sid}: describe-only section was split; kept as a leaf")
            new = []
        kept, taken = [], set()
        for grp in new:
            name = grp.get("name")
            try:
                ids = list(dict.fromkeys(parse_items(grp.get("items"), n)))
            except ValueError as e:
                errors.append(f"{sid} new {name!r}: {e}")
                continue
            if not str(name or "").strip():
                errors.append(f"{sid}: new submodel without a name")
            if err := description_error(grp.get("description")):
                errors.append(f"{sid} new {name!r}: {err}")
            grp["keywords"] = [k for k in grp.get("keywords") or [] if k in KEYWORDS]
            free = [k for k in ids if k not in taken]
            if len(free) < len(ids):
                notes.append(f"{sid} new {name!r}: items already in an earlier submodel stay there")
            if len(free) < 2:
                notes.append(f"{sid} new {name!r}: fewer than 2 items; dropped")
                continue
            taken.update(free)
            grp["items"] = format_items(free)
            kept.append(grp)
        if len(kept) == 1 and len(taken) == n:
            notes.append(f"{sid}: a single child with every item is the section itself; kept as a leaf")
            kept = []
        sp["new"] = kept
        dest = {k: g for g, grp in enumerate(kept) for k in parse_items(grp["items"], n)}
        groups = []
        for grp in sp.get("groups") or []:
            comment = str(grp.get("comment", ""))
            try:
                ids = parse_items(grp.get("items"), n)
            except ValueError:
                ids = []
            if not ids or sorted(set(ids)) != list(range(min(ids), max(ids) + 1)) \
                    or len({dest.get(k) for k in ids}) > 1 \
                    or not comment.strip() or "*/" in comment or any(ord(c) < 32 for c in comment):
                notes.append(f"{sid} group {comment!r}: not one run of consecutive items in one place; dropped")
                continue
            groups.append(grp)
        sp["groups"] = groups
    errors += [f"{sid}: missing from the answer" for sid in section_ids if sid not in seen]
    return errors, notes


def apply_split(ir, plan):
    """Apply a repaired split plan; returns the ids of the new child sections.

    A split section whose remaining loose parts are still many (REOFFER_PARTS) is offered again
    in the next round (at most MAX_PASSES decisions per section), so big flat sections can be
    structured in several passes instead of one huge answer.
    """
    created = []
    for sp in plan["sections"]:
        s = ir["sections"][sp["id"]]
        s["desc"], s["keywords"], s["todo"] = sp["description"].strip(), sp.get("keywords") or [], False
        if s.get("rename") and sp.get("rename"):
            mpd.rename(ir, s, sp["rename"])
            s["rename"] = False
        n = len(mpd.items(s))
        new = sp.get("new") or []
        dest = {k: g for g, grp in enumerate(new) for k in parse_items(grp["items"], n)}
        comments = {min(parse_items(grp["items"], n)): f"0 /* {grp['comment'].strip()} */"
                    for grp in sp.get("groups") or []}
        # Children are created in order of their first item, so ids/names are deterministic.
        order = sorted(range(len(new)), key=lambda g: min(k for k, d in dest.items() if d == g))
        kids = {}
        for g in order:
            grp = new[g]
            kid = mpd.new_section(ir, mpd.unique_name(ir, grp["name"]), desc=grp["description"].strip(),
                                  keywords=grp.get("keywords") or [])
            kids[g] = kid
            created.append(kid["id"])
        body, k, steps, last_step = [], 0, 0, {}
        for e in s["body"]:
            if isinstance(e, str):
                steps += bool(mpd.STEP.match(e))
                body.append(e)
                continue
            g = dest.get(k)
            target = body if g is None else kids[g]["body"]
            if g is not None:
                if not kids[g]["body"]:
                    body.append(mpd.identity_ref(kids[g]["name"]))  # where its first item was
                elif steps > last_step[g]:
                    target.append("0 STEP")  # keep the build-step boundaries inside the child
                last_step[g] = steps
            if k in comments:
                target.append(comments[k])
            target.append(e)
            k += 1
        s["body"] = collapse_steps(body) if new else body
        s["state"] = "split" if new else "leaf"
        s["passes"] = s.get("passes", 0) + 1
        if new and loose_parts(ir, s) >= REOFFER_PARTS and s["passes"] < MAX_PASSES:
            s["state"], s["todo"] = "open", True
        for kid in kids.values():
            big = len(mpd.items(kid)) >= mpd.MIN_SPLIT_ITEMS
            kid["state"], kid["todo"] = ("open", True) if big else ("leaf", False)
    return created


def finalize_names(ir):
    """Sections still flagged with a meaningless name (the agent gave none, or its unit failed)
    get one from their description, if that yields a meaningful name."""
    for s in list(ir["sections"].values()):
        slug = slug_from(s.get("desc") or "")
        if s.get("rename") and slug and not mpd.meaningless(slug):
            mpd.rename(ir, s, slug)
            s["rename"] = False


def slug_from(description):
    """A short name from a description: its head noun phrase, e.g. 'Windscreen and seat assembly
    with tan details' -> 'windscreen-and-seat-assembly'."""
    head = re.split(r"[,:;(]| with | of | on | for | in | at ", description, maxsplit=1)[0]
    words = [w for w in re.findall(r"[a-z0-9]+", head.lower()) if w not in ("a", "an", "the")]
    return "-".join(words[:4])


def loose_parts(ir, s):
    """Items of a section that are parts, not references to submodels."""
    names = mpd.section_by_name(ir)
    return sum(1 for i in mpd.items(s) if i["ref"] not in names or names[i["ref"]]["kind"] == "part")


def collapse_steps(body):
    """Drop step markers left with no item before them (steps emptied by moving items out)."""
    out, since_step = [], False
    for e in body:
        if isinstance(e, str) and mpd.STEP.match(e):
            if since_step:
                out.append(e)
            since_step = False
        else:
            since_step |= isinstance(e, dict)
            out.append(e)
    return out


def repair_refine(ir, section_ids, plan):
    """Check a refine answer; returns (errors, notes) like repair_split()."""
    errors = []
    for sp in plan.get("sections", []):
        if sp.get("id") not in section_ids:
            errors.append(f"{sp.get('id')}: not a section of this task")
        elif err := description_error(sp.get("description")):
            errors.append(f"{sp['id']}: {err}")
    return errors, []


def apply_refine(ir, plan):
    for sp in plan["sections"]:
        ir["sections"][sp["id"]]["desc"] = sp["description"].strip()
