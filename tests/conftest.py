import os
from pathlib import Path

# The tests use the repository's LDraw info DB unless LDRAW_ANNOTATOR_DB points elsewhere.
os.environ.setdefault("LDRAW_ANNOTATOR_DB", str(Path(__file__).resolve().parents[1] / "data" / "ldraw-info.db"))
