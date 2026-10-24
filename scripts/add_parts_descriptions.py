#!/usr/bin/env python3
"""
This script adds parts descriptions as comments to LDraw files: one "0 // <description> (<colour>)"
line directly above every part reference (type-1 line), e.g.

    0 // Plate  2 x  2 (black)
    1 0 80 8 0 1 0 0 0 1 0 0 0 1 3022.dat

Descriptions come from data/ldraw-info.db: table PARTS_DESCRIPTIONS(part, description) for parts
and table COLORS(code, color, value) for colour names (lowercased LDConfig names, e.g. trans_clear).

- Idempotent: a part whose line above already is exactly that comment is left alone, so the
  script can run on partly annotated files. Other comments (author notes) are kept.
- A part preceded by "0 BFC INVERTNEXT" gets its comment above that line, because INVERTNEXT
  must stay directly before the part it applies to.
- Embedded parts ("0 FILE xxx.dat" sections of an MPD) are not annotated: their insides are
  geometry, not model content.
- Unknown parts are left without comment; unknown colours keep their numeric code.
- Line endings, UTF-8 BOM and undecodable bytes are preserved.

Usage:
    python add_parts_descriptions.py <ldraw_file | ldraw_models_dir> <ldraw_file_with_descriptions | ldraw_files_with_descriptions_dir>

The ldraw-annotator pipeline imports this module and calls annotate_lines() on model sections.
"""

import io
import os
import sqlite3
import sys
from pathlib import Path


DB_FILE = Path(__file__).resolve().parents[1] / "data" / "ldraw-info.db"

PARTS_DESCRIPTIONS = {}  # normalized part name -> description
COLOURS_DESCRIPTIONS = {}  # colour code -> lowercase colour name


def normalized(name):
    """LDraw file names are case-insensitive and may use either slash."""
    return name.strip().replace("\\", "/").lower()


def cache_descriptions_db(db_file_path=DB_FILE):
    """Load part and colour descriptions once per process."""
    if PARTS_DESCRIPTIONS:
        return
    conn = sqlite3.connect(Path(db_file_path).as_uri() + "?mode=ro", uri=True)
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


def process_ldraw_file(ldraw_file_path, output_file_path):
    data = Path(ldraw_file_path).read_bytes()
    bom = data.startswith(b"\xef\xbb\xbf")
    text = data[3 if bom else 0:].decode("utf-8", "surrogateescape")
    # newline="" splits on \n, \r\n and \r while keeping each line's own ending.
    lines = list(io.StringIO(text, newline=""))

    output, section, in_embedded_part = [], [], False
    for line in lines:
        words = line.split(None, 2)
        if words[:2] in (["0", "FILE"], ["0", "NOFILE"]):
            output += section if in_embedded_part else annotate_lines(section)
            section = []
            in_embedded_part = words[1] == "FILE" and normalized(line.split(None, 2)[2]).endswith(".dat")
        section.append(line)
    output += section if in_embedded_part else annotate_lines(section)

    data = (b"\xef\xbb\xbf" if bom else b"") + "".join(output).encode("utf-8", "surrogateescape")
    Path(output_file_path).write_bytes(data)


def process_ldraw_directory(ldraw_dir_path, output_dir_path):
    for root, _, files in os.walk(ldraw_dir_path):
        for file in files:
            if file.lower().endswith(('.ldr', '.mpd', '.dat')):
                ldraw_file_path = os.path.join(root, file)
                relative_path = os.path.relpath(ldraw_file_path, ldraw_dir_path)
                output_file_path = os.path.join(output_dir_path, relative_path)
                os.makedirs(os.path.dirname(output_file_path), exist_ok=True)
                process_ldraw_file(ldraw_file_path, output_file_path)


def main():
    if len(sys.argv) < 3:
        print("Usage: add_parts_descriptions.py <ldraw_file | ldraw_files_dir> <ldraw_file_with_descriptions | ldraw_files_with_descriptions_dir>")
        print("If the input is a directory, all LDraw files in that directory will be processed.")
        print("If the input is a directory, the output must be an existing directory, or one will be created, and the processed files will be saved there with the same names as the input files.")
        print("If the input is a directory and the output is a file, an error will be raised.")
        sys.exit(1)

    ldraw_src_path = sys.argv[1]
    output_dest_path = sys.argv[2]

    if not DB_FILE.is_file():
        print(f"Error: File '{DB_FILE}' does not exist.")
        sys.exit(1)

    if not os.path.exists(ldraw_src_path):
        print(f"Error: File or directory '{ldraw_src_path}' does not exist.")
        sys.exit(1)

    # Print status messages to stderr to avoid interfering with the output file
    print(f"Loading parts and colours descriptions from '{DB_FILE}'...", file=sys.stderr)
    cache_descriptions_db()

    if os.path.isdir(ldraw_src_path):
        if os.path.isfile(output_dest_path):
            print("Error: Output is an existing file, if the input is a directory, the output must be an existing directory or one will be created.")
            sys.exit(1)
        os.makedirs(output_dest_path, exist_ok=True)
        process_ldraw_directory(ldraw_src_path, output_dest_path)
    elif os.path.isdir(output_dest_path):
        process_ldraw_file(ldraw_src_path, os.path.join(output_dest_path, os.path.basename(ldraw_src_path)))
    else:
        process_ldraw_file(ldraw_src_path, output_dest_path)

    print(f"Finished adding parts descriptions to '{ldraw_src_path}'...", file=sys.stderr)


if __name__ == "__main__":
    main()
