"""Flexible parts (hoses, cables, axles, chains, strings, springs...): detected and isolated
deterministically, with no LLM.

A flexible part is drawn as many small segments, synthesized from a few constraints:
  - LSynth:  "0 SYNTH BEGIN <TYPE> <colour>" ... "0 SYNTH END" blocks inside a model section,
             or bare runs of LSynth segment parts when the SYNTH meta lines were stripped.
  - LDCad:   whole sections marked "0 !LDCAD GENERATED" + "0 !LDCAD CONTENT [type=path|spring]".
Each becomes its own flex submodel with one description and "0 !KEYWORDS flexible, <kind>".
Segments get no part comments and no shape description. See ../lsynth-nova/README.md.
"""

import re

from . import mpd

# Segment parts per kind, from ../lsynth-nova/lsynth_nova/data/lsynth.mpd (hose/band definitions),
# including the newer names of renamed parts (e.g. 79 -> 71944k01).
SEGMENTS = {
    "pneumatic hose": "ls20 ls21 ls22 ls23",
    "ribbed hose": "79 80 71944k01 71944k02",
    "hose": "755 756 758 759 30527k01 30527k02 ls90 ls91 57539a 57539b",
    "axle": "ls40 ls41",
    "flex-system hose": "ls50 ls51",
    "cable": "ls10 ls11 ls12 ls70 ls71 342",
    "fiber optic cable": "ls30",
    "string": "ls60 ls61",
    "minifig chain": "209 30104k02",
    "chain": "3711 273",
    "tread": "3873 57518 680 681 682 71372k01 71372k02 71372k03 ls80",
}
KIND_OF = {f"{p}.dat": kind for kind, parts in SEGMENTS.items() for p in parts.split()}
MIN_RUN_LSYNTH = 2  # "LSxx" parts only exist as synthesized segments
MIN_RUN_GENERIC = 8  # chain links, hose segments... can also be used alone

# LSynth type names -> kind (prefix match on the type, e.g. TECHNIC_PNEUMATIC_HOSE_BEVELED).
SYNTH_KINDS = [("PNEUMATIC", "pneumatic hose"), ("RIBBED", "ribbed hose"), ("AXLE", "axle"),
               ("FLEX-SYSTEM_HOSE", "flex-system hose"), ("FLEX_SYSTEM_HOSE", "flex-system hose"),
               ("FIBER", "fiber optic cable"), ("CABLE", "cable"), ("HOSE", "hose"),
               ("STRING", "string"), ("CHAIN", "chain"), ("TREAD", "tread"), ("CRAWLER", "tread"),
               ("BAND", "rubber band"), ("BELT", "rubber belt"), ("ARC", "brick arc")]

# LDCad section-name hints -> kind (checked in order, on the lowercased name).
LDCAD_KINDS = [("ribbedhose", "ribbed hose"), ("ribhose", "ribbed hose"), ("flexaxle", "axle"),
               ("rubberband", "rubber band"), ("band", "rubber band"), ("string", "string"),
               ("rope", "string"), ("chain", "chain"), ("tread", "tread"), ("cable", "cable"),
               ("wire", "cable"), ("hose", "hose"), ("shock", "shock absorber spring"),
               ("spring", "spring")]

SYNTH_BEGIN = re.compile(r"0 (WRITE )?SYNTH BEGIN (\S+)(?: (\S+))?")
SYNTH_END = re.compile(r"0 (WRITE )?SYNTH END\b")


def synth_kind(synth_type):
    t = synth_type.upper()
    return next((kind for key, kind in SYNTH_KINDS if key in t), "part")


def describe_ldcad(section, lines):
    """Describe an LDCad-generated section in place from its CONTENT type and name."""
    name = section["name"].lower().replace("-", "").replace("_", "")
    is_spring = any("[type=spring]" in l for l in lines if l.startswith("0 !LDCAD CONTENT"))
    kind = next((k for key, k in LDCAD_KINDS if key in name), "spring" if is_spring else "part")
    section["desc"] = f"Flexible {kind} (LDCad-generated)"
    section["keywords"] = ["flexible", kind]
    section["has_title"] = len(lines) > 1 and mpd.is_title(lines[1])


def _add_flex(ir, kind, desc, block):
    """Create a flex section holding block (verbatim lines); return the reference item lines."""
    name = mpd.unique_name(ir, "flex-" + kind)
    s = mpd.new_section(ir, name, kind="flex", desc=desc, body=list(block), keywords=["flexible", kind])
    s["state"] = "leaf"
    return mpd.identity_ref(name)["lines"][0]


def extract(ir, section, body):
    """Move LSynth blocks and segment runs out of a model section's body lines.

    Returns the new body, with an identity reference to each new flex section in place of the
    block, so world positions and colours are unchanged.
    """
    out, i = [], 0
    while i < len(body):
        m = SYNTH_BEGIN.match(body[i])
        if m and m.group(2) != "DEFINE":
            j = i + 1
            while j < len(body) and not SYNTH_END.match(body[j]):
                j += 1
            start, end = i, min(j + 1, len(body))
            # Keep an LPUB parts-list wrapper together with its block.
            if out and out[-1].startswith("0 LPUB PLI BEGIN"):
                start -= 1
                out.pop()
            if end < len(body) and body[end].startswith("0 LPUB PLI END"):
                end += 1
            kind = synth_kind(m.group(2))
            colour = mpd.apd.COLOURS_DESCRIPTIONS.get(m.group(3) or "", "")
            desc = f"Flexible {kind}{', ' + colour if colour else ''} (LSynth-synthesized)"
            out.append(_add_flex(ir, kind, desc, body[start:end]))
            i = end
            continue
        # A run of consecutive segment parts of one kind (LSynth output without SYNTH lines).
        kind = _segment_kind(body[i])
        if kind:
            j = i
            while j < len(body) and _segment_kind(body[j]) == kind:
                j += 1
            lsynth = any(mpd.type1(l)[2].lower().startswith("ls") for l in body[i:j])
            if j - i >= (MIN_RUN_LSYNTH if lsynth else MIN_RUN_GENERIC):
                out.append(_add_flex(ir, kind, f"Flexible {kind} made of {j - i} segments", body[i:j]))
                i = j
                continue
            out += body[i:j]
            i = j
            continue
        out.append(body[i])
        i += 1
    return out


def _segment_kind(line):
    t = mpd.type1(line)
    return KIND_OF.get(mpd.normalized(t[2])) if t else None
