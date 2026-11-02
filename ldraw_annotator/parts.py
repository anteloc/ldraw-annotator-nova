"""Part comments: one "0 // <description> (<colour>)" line directly above every part reference
(type-1 line), e.g.

    0 // Plate  2 x  2 (black)
    1 0 80 8 0 1 0 0 0 1 0 0 0 1 3022.dat

Descriptions come from the LDraw info DB (--db or $LDRAW_ANNOTATOR_DB): table
PARTS_DESCRIPTIONS(part, description) for parts and table COLORS(code, color, value) for colour
names (lowercased LDConfig names, e.g. trans_clear).

- Idempotent: a part whose line above already is exactly that comment is left alone, so partly
  annotated files are fine. Other comments (author notes) are kept.
- A part preceded by "0 BFC INVERTNEXT" gets its comment above that line, because INVERTNEXT
  must stay directly before the part it applies to.
- Unknown parts are left without comment; unknown colours keep their numeric code.
"""

import os
import sqlite3
from pathlib import Path

DB_ENV = "LDRAW_ANNOTATOR_DB"
db_file = None  # set from --db by the CLI; falls back to $LDRAW_ANNOTATOR_DB

PARTS_DESCRIPTIONS = {}  # normalized part name -> description
COLOURS_DESCRIPTIONS = {}  # colour code -> lowercase colour name


def db_path():
    """The LDraw info DB: --db, else $LDRAW_ANNOTATOR_DB."""
    path = db_file or os.environ.get(DB_ENV)
    if not path:
        raise ValueError(f"no LDraw info DB: pass --db PATH or set {DB_ENV}")
    path = Path(path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"LDraw info DB not found: {path}")
    return path


def connect_ro():
    return sqlite3.connect(db_path().resolve().as_uri() + "?mode=ro", uri=True)


def normalized(name):
    """LDraw file names are case-insensitive and may use either slash."""
    return name.strip().replace("\\", "/").lower()


def cache_descriptions_db():
    """Load part and colour descriptions once per process."""
    if PARTS_DESCRIPTIONS:
        return
    conn = connect_ro()
    try:
        for part_name, description in conn.execute("SELECT part, description FROM PARTS_DESCRIPTIONS"):
            PARTS_DESCRIPTIONS[normalized(part_name)] = description
        for code, colour_name, _value in conn.execute("SELECT code, color, value FROM COLORS"):
            COLOURS_DESCRIPTIONS[code] = colour_name.lower()
    finally:
        conn.close()


def desc_line_for(ldraw_file_line, extra_descriptions=None):
    """Return the comment line (without line ending) for a type-1 line, or None.

    extra_descriptions maps normalized names to descriptions for parts the DB doesn't know,
    e.g. parts embedded in the MPD itself.
    """
    line_items = ldraw_file_line.split(None, 14)
    if len(line_items) < 15 or line_items[0] != "1":
        return None
    part_name = normalized(line_items[14])
    description = PARTS_DESCRIPTIONS.get(part_name)
    if description is None and extra_descriptions:
        description = extra_descriptions.get(part_name)
    if description is None:
        return None
    colour_code = line_items[1]
    colour_description = COLOURS_DESCRIPTIONS.get(colour_code, colour_code)
    return f"0 // {description} ({colour_description})"


def annotate_lines(lines, extra_descriptions=None):
    """Return lines with a description comment above every known part, unless already there.

    Lines may keep their line endings; inserted comments reuse the ending of their part line.
    """
    output = []
    for line in lines:
        comment = desc_line_for(line, extra_descriptions)
        if comment is not None:
            ending = line[len(line.rstrip("\r\n")):]
            # Insert above any "0 BFC INVERTNEXT" that belongs to this part.
            at = len(output)
            while at and output[at - 1].split()[:3] == ["0", "BFC", "INVERTNEXT"]:
                at -= 1
            if not (at and output[at - 1].rstrip("\r\n") == comment):
                output.insert(at, comment + ending)
        output.append(line)
    return output
