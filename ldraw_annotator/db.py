"""db-sync (run by the user, not by agents): store annotated models in data/ldraw-info.db.

  MODELS_DESCRIPTIONS(model, description)                 root description per model file
  SUBMODELS_DESCRIPTIONS(model, submodel, description)    every model/flex section but the root
  SUBMODELS_REFERENCES(model, parent, child, count)       the hierarchy (created if missing)

A model's previous rows are replaced. The existing triggers keep the *_FTS tables in sync.
"""

import sqlite3
from collections import Counter

from . import check, mpd

REFERENCES_TABLE = """CREATE TABLE IF NOT EXISTS SUBMODELS_REFERENCES (
    model  VARCHAR NOT NULL,
    parent VARCHAR NOT NULL,
    child  VARCHAR NOT NULL,
    count  INTEGER NOT NULL,
    PRIMARY KEY (model, parent, child)
)"""


def description(section):
    lines = section["lines"]
    return lines[1].split(None, 1)[1].strip() if len(lines) > 1 and mpd.is_title(lines[1]) else ""


def sync(files):
    conn = sqlite3.connect(mpd.apd.DB_FILE)
    report = []
    try:
        conn.execute(REFERENCES_TABLE)
        for path in files:
            secs, order = check.sections(path)
            model = path.name
            tree = [k for k in order if secs[k]["kind"] in ("model", "flex")]
            conn.execute("INSERT INTO MODELS_DESCRIPTIONS(model, description) VALUES (?, ?) "
                         "ON CONFLICT(model) DO UPDATE SET description = excluded.description",
                         (model, description(secs[order[0]])))
            conn.execute("DELETE FROM SUBMODELS_DESCRIPTIONS WHERE model = ?", (model,))
            conn.execute("DELETE FROM SUBMODELS_REFERENCES WHERE model = ?", (model,))
            conn.executemany("INSERT INTO SUBMODELS_DESCRIPTIONS(model, submodel, description) VALUES (?, ?, ?)",
                             [(model, secs[k]["name"], description(secs[k])) for k in tree[1:]])
            refs = []
            for k in tree:
                counts = Counter(mpd.normalized(t[2]) for l in secs[k]["lines"] if (t := mpd.type1(l)))
                refs += [(model, secs[k]["name"], secs[c]["name"], n) for c, n in counts.items()
                         if c in secs and secs[c]["kind"] in ("model", "flex")]
            conn.executemany("INSERT INTO SUBMODELS_REFERENCES(model, parent, child, count) VALUES (?, ?, ?, ?)", refs)
            report.append({"model": model, "submodels": len(tree) - 1, "references": len(refs)})
        conn.commit()
    finally:
        conn.close()
    return report
