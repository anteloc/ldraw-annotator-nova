"""Deterministic parts of the pipeline (no LLM): reading, part comments, flexible parts, plans,
writing and checking. Run: .venv/bin/python -m pytest"""

from pathlib import Path

from ldraw_annotator import check, digest, mpd, plan

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def write_and_check(ir, src, tmp_path, name="out.mpd"):
    out = tmp_path / name
    mpd.write(ir, out)
    return out, check.check(src, out)


def test_part_comments_match_the_canonical_example(tmp_path):
    ir = mpd.load(DOCS / "106-1.mpd")
    out, report = write_and_check(ir, DOCS / "106-1.mpd", tmp_path)
    assert report["ok"], report["errors"]
    ours = [l for l in out.read_text().splitlines() if l.startswith("0 //")]
    expected = [l for l in (DOCS / "106-1-annotated.mpd").read_text().splitlines() if l.startswith("0 //")]
    assert ours == expected


def test_part_comments_are_idempotent_and_keep_invertnext_last():
    mpd.apd.cache_descriptions_db()
    lines = ["0 BFC INVERTNEXT", "1 0 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat", "0 // author note",
             "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat"]
    once = mpd.apd.annotate_lines(lines)
    assert once[:3] == ["0 // Brick  2 x  4 (black)", "0 BFC INVERTNEXT", lines[1]]
    assert once[3:] == ["0 // author note", "0 // Brick  2 x  4 (red)", lines[3]]
    assert mpd.apd.annotate_lines(once) == once


def test_crlf_and_bom_are_preserved(tmp_path):
    src = tmp_path / "bom.ldr"
    src.write_bytes(b"\xef\xbb\xbf0 Tiny\r\n1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\r\n")
    out, report = write_and_check(mpd.load(src), src, tmp_path)
    assert report["ok"], report["errors"]
    data = out.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf0 FILE bom.ldr\r\n0 Tiny\r\n")
    assert b"\n" not in data.replace(b"\r\n", b"")


def test_lsynth_block_becomes_a_flex_submodel(tmp_path):
    src = DOCS / "LSynth_Tutorial_Synthesis.ldr"
    ir = mpd.load(src)
    flex = [s for s in ir["sections"].values() if s["kind"] == "flex"]
    assert len(flex) == 1 and flex[0]["keywords"] == ["flexible", "pneumatic hose"]
    out, report = write_and_check(ir, src, tmp_path)
    assert report["ok"], report["errors"]
    text = out.read_text()
    assert "0 SYNTH BEGIN TECHNIC_PNEUMATIC_HOSE 1" in text.split("flex-pneumatic-hose.ldr", 2)[2]
    assert "// ~LSynth" not in text  # no per-segment comments


def test_split_moves_items_keeps_steps_and_is_lossless(tmp_path):
    ir = mpd.load(DOCS / "106-1.mpd")
    answer = {"sections": [{"id": "S2", "description": "Blue car", "new": [
        {"name": "chassis", "items": "0-5,44-49", "description": "Chassis with wheels", "keywords": ["chassis"]},
        {"name": "cabin", "items": "34-40", "description": "Doors and roof"}],
        "groups": [{"items": "20-23", "comment": "headlights"}]}]}
    assert plan.repair_split(ir, ["S2"], answer) == ([], [])
    created = plan.apply_split(ir, answer)
    assert [ir["sections"][c]["name"] for c in created] == ["106 - chassis.ldr", "106 - cabin.ldr"]
    out, report = write_and_check(ir, DOCS / "106-1.mpd", tmp_path)
    assert report["ok"], report["errors"]
    lines = out.read_text().splitlines()
    files = [i for i, l in enumerate(lines) if l.startswith("0 FILE")]
    assert all(lines[i - 1] == "0 STEP" for i in files[1:])  # every submodel starts a build step
    assert "0 /* headlights */" in lines
    assert "0 !KEYWORDS chassis" in lines


def test_bad_ranges_are_errors_and_harmless_problems_are_repaired():
    ir = mpd.load(DOCS / "106-1.mpd")
    answer = {"sections": [{"id": "S2", "description": "Car", "new": [
        {"name": "far", "items": "48-60", "description": "x"},
        {"name": "one", "items": "3", "description": "x"}]}]}
    errors, notes = plan.repair_split(ir, ["S2"], answer)
    assert any("outside" in e for e in errors)
    assert any("fewer than 2 items" in n for n in notes)
    answer = {"sections": [{"id": "S2", "description": "Car", "new": [
        {"name": "all", "items": "0-49", "description": "x"}]}]}
    assert plan.repair_split(ir, ["S2"], answer)[0] == [] and answer["sections"][0]["new"] == []
    answer = {"sections": [{"id": "S2", "description": "Car", "new": [
        {"name": "a", "items": "0-5", "description": "x"}, {"name": "b", "items": "4-9", "description": "y"}]}]}
    assert plan.repair_split(ir, ["S2"], answer)[0] == []
    assert answer["sections"][0]["new"][1]["items"] == "6-9"


def test_describe_only_sections_are_not_split():
    ir = mpd.load(DOCS / "106-1.mpd")  # S3 (wheel assembly) has 2 items: a leaf without asking
    assert ir["sections"]["S3"]["state"] == "leaf"
    answer = {"sections": [{"id": "S3", "description": "Wheel",
                            "new": [{"name": "x", "items": "0-1", "description": "y"}]}]}
    errors, notes = plan.repair_split(ir, ["S3"], answer)
    assert not errors and answer["sections"][0]["new"] == [] and "describe-only" in notes[0]


def test_check_detects_a_lost_part(tmp_path):
    ir = mpd.load(DOCS / "106-1.mpd")
    body = ir["sections"]["S4"]["body"]
    body.remove(mpd.items(ir["sections"]["S4"])[-1])  # drop the minifig's last part
    _, report = write_and_check(ir, DOCS / "106-1.mpd", tmp_path)
    assert not report["ok"] and any("not lossless" in e for e in report["errors"])


def test_digest_lists_items_and_steps():
    ir = mpd.load(DOCS / "106-1.mpd")
    text = digest.unit_digest(ir, ["S2"])
    assert 'i19 > S3 "106 - wheel assembly.ldr" [2 items]' in text
    assert "i0 Plate  2 x 10 (black) @0,0,0" in text and "--- step" in text


def test_meaningless_names_are_renamed_with_their_references(tmp_path):
    src = tmp_path / "toy.mpd"
    src.write_text("0 FILE main.ldr\n0 main\n0 Name: main.ldr\n"
                   "1 16 0 0 0 1 0 0 0 1 0 0 0 1 step 2.ldr\n1 4 40 0 0 1 0 0 0 1 0 0 0 1 Step 2.ldr\n"
                   "0 FILE step 2.ldr\n0 step 2\n0 Name: step 2.ldr\n"
                   "1 16 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n1 1 0 -24 0 1 0 0 0 1 0 0 0 1 3001.dat\n")
    ir = mpd.load(src)
    assert ir["sections"]["S2"]["rename"] and not ir["sections"]["S1"]["rename"]
    answer = {"sections": [{"id": "S2", "description": "Two stacked bricks", "rename": "brick-stack", "new": []}]}
    assert plan.repair_split(ir, ["S2"], answer)[0] == []
    plan.apply_split(ir, answer)
    out, report = write_and_check(ir, src, tmp_path)
    assert report["ok"], report["errors"]
    text = out.read_text()
    assert "step 2" not in text.lower()
    assert text.count("toy - brick-stack.ldr") == 4  # 2 references, FILE line, Name: line


def test_rate_limited_calls_wait_without_using_an_attempt(tmp_path, monkeypatch):
    import threading
    from ldraw_annotator import run
    runner = object.__new__(run.Runner)  # no Cline needed: the call itself is replaced
    runner.log_lock = threading.Lock()
    answer = {"sections": [{"id": "S3", "description": "Wheel: grey rim with black tyre", "new": []}]}
    replies = iter([(None, {"error": "cline call failed: Rate limit reached for requests"}),
                    (None, {"error": "cline call failed: Rate limit reached for requests"}),
                    (answer, {"error": None})])
    runner.call = lambda kind, schema, text: next(replies)
    monkeypatch.setattr(run.time, "sleep", lambda seconds: None)
    ir = mpd.load(DOCS / "106-1.mpd")
    result = runner.unit(tmp_path, ir, "r1-u00", ["S3"], "split")
    assert result == answer
    log = [l for l in (tmp_path / "log.jsonl").read_text().splitlines()]
    assert len(log) == 3 and all('"attempt": 1' in l for l in log)
