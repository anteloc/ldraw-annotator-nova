# Output contract: annotated LDraw models

This is what every `.mpd` produced by ldraw-annotator-nova guarantees. It's written for the
agents and tools that consume these files:
- to **learn from them** in order to generate new models;
- to **understand their internals** in order to animate their mechanisms.

`./ldraw-annotator check <input> <output>` verifies every rule marked ✔.

## 1. One self-contained MPD

- ✔ All submodels are `0 FILE` sections of the same file.
- The first section is the main model. A plain `.ldr` input gets a root `0 FILE <input file name>`.
- Embedded parts (`.dat` sections, unofficial parts, generated meshes) stay in the file, unchanged.

## 2. Section header

```
0 FILE 106 - car.ldr
0 Blue car body with UNICEF logo panels, windscreen, hinged roof, doors and four wheels
0 Name: 106 - car.ldr
0 Author: ...
0 !KEYWORDS body
```

- ✔ The line right after `0 FILE` is the section's **description**: one line of plain text that
  says what the submodel looks like and does in its parent.
  - It never starts with `//`, `/*`, `!`, `Name:`, `Author:`, `BFC`, `FILE`, `STEP` or other meta
    commands, so readers that take "the first comment after FILE" get it.
  - That includes pyldraw3 `Model.description` ✔.
  - It replaces the original title line (e.g. `0 car`).
- The original header lines follow, unchanged.
- New sections get `0 Name:`, plus `0 Author:` and `0 !LICENSE` copied from the main model.
- Optional `0 !KEYWORDS` with 1-3 function tags (section 9) comes at the end of the header.

## 3. Build steps

- ✔ Every model section's `0 FILE`, except the first, is directly preceded by `0 STEP`. Each
  submodel therefore starts a new step (a new chapter in building instructions), instead of
  continuing the previous submodel's last step.
- The original `0 STEP` / `0 ROTSTEP` lines are kept.
  - When parts move into a new submodel, the step boundaries between them move along.
  - A step left empty in the parent is removed.

## 4. Part comments

```
0 // Plate  2 x  2 (black)
1 0 80 8 0 1 0 0 0 1 0 0 0 1 3022.dat
```

- ✔ Every part reference in a model section has `0 // <catalogue description> (<colour>)`
  directly above it.
  - Colour names are lowercase LDConfig names (`trans_clear`, `rubber_black`). Unknown or direct
    colours keep their code.
- If the part has `0 BFC INVERTNEXT`, the comment goes above that line, because INVERTNEXT must
  stay right before its part.
- References to submodels get no part comment. The referenced section's description says what it
  is.
- Comments that were already there (author notes) are kept.

## 5. Group comments

```
0 /* headlights */
```

- An optional one-line comment above a run of consecutive parts that's worth naming but isn't a
  submodel of its own.
- Never placed directly after `0 FILE`.

## 6. Hierarchy

- ✔ Submodels are real `0 FILE` sections referenced by type-1 lines. A section can reference other
  submodels as well as parts.
- New submodels live in their parent's frame and are referenced with colour 16 and the identity
  matrix: `1 16 0 0 0 1 0 0 0 1 0 0 0 1 <name>`. The parts inside keep their exact placement and
  colour.
- ✔ **Lossless**:
  - every physical part occurrence appears exactly once, with an unchanged world transform and
    effective colour;
  - no cycles.
- Depth is **semantic**: a submodel is split only into assemblies that make sense (moving parts,
  functional units, minifigs…). There's no fixed number of levels.
- Existing sections are kept; meaningless names are replaced (section 7). Unreferenced (orphan)
  sections of the input are kept and described, but they aren't part of the main model's tree.

## 7. Names

- New sections are named `<prefix> - <kebab-case name>.ldr`, e.g. `8466 - front-axle.ldr`.
- The prefix is the one shared by the input's section names, otherwise the set number or file name.
- Existing names that say what the section is are kept.
- Meaningless existing names are renamed the same way, and every reference and `0 Name:` line
  follows. That covers numbers (`10174_4.ldr`), codes (`8466ta4.ldr`), build-step numbers
  (`house-step36.ldr`), and `subModel-3` / `Untitled`.
- The main model and unreferenced (orphan) sections keep their names.

## 8. Order

- ✔ Natural reading order: model sections in DFS pre-order from the root (a parent comes before
  its children, recursively; a shared submodel appears where it's first used).
- After the model sections come orphans, then flexible-part sections, then embedded parts.

## 9. Function tags

`0 !KEYWORDS <tags>` lets tools find mechanisms and structures without parsing prose. The tags come
from a fixed list:

`steering, suspension, gearbox, differential, drivetrain, engine, piston, turntable, winch, crane,
boom, hinge, door, wheel, landing-gear, propeller, minifig, chassis, frame, body, interior, base`

Flexible parts always have `0 !KEYWORDS flexible, <kind>` (section 10). Input `!KEYWORDS` lines are
kept.

## 10. Flexible parts

- Hoses, cables, flexible axles, strings, chains, treads, rubber bands and springs are each a
  submodel of their own:
  - LSynth blocks and runs of LSynth segment parts are moved into `<prefix> - flex-<kind>.ldr`;
  - LDCad-generated sections already are separate.
- Each has one description, e.g. `Flexible pneumatic hose, blue (LSynth-synthesized)`, and
  `0 !KEYWORDS flexible, <kind>`.
- ✔ Their segments are unchanged, with no per-segment comments and no shape description.

## 11. Preserved

- ✔ Embedded part sections are byte-identical.
- UTF-8 BOM, line endings (the input's dominant style for rewritten lines), undecodable bytes,
  attribution, licence, and BFC/TEXMAP/LDCad metadata attached to their parts.
