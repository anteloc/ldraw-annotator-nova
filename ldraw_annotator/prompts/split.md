# Task: structure a LEGO model into meaningful submodels

You get a digest of one LEGO model (LDraw format) listing some of its sections (submodels).
For EVERY listed section, return in the structured output:

- `id`: the section id (e.g. `S4`).
- `description`: one line saying what the section is, as a person looking at it would describe it.
- `keywords`: 0-3 function tags from the allowed list, only when they clearly apply.
- `new`: your decision.
  - `[]` means **leaf**: the section is one semantic unit; nobody will split it further.
  - Otherwise, a list of new child submodels. Each has:
    - `items`: item ids as ranges (`"0-7,12"`);
    - `name`: a short kebab-case name (`front-axle`); the tool adds the model prefix and `.ldr`;
    - `description`: one line;
    - optional `keywords`.

    Items you don't list stay in the section itself.
  - Only sections marked `decide: leaf, or split one level` may get new submodels. Answer `[]`
    for sections marked `describe only`.
- `rename`: only for sections marked "its name says nothing". Give a short kebab-case name that
  says what the section is (`front-door-frame`, `left-leg`), like the names of new submodels.
- `groups` (optional): one-line comments for a run of consecutive items (`"3-9"`) that is worth
  naming but not worth a submodel, e.g. `{"items": "12-17", "comment": "left wall windows"}`.

## Rules

- **Never force a split.** Leaf is a normal, good answer. Split only when the section really
  contains distinct assemblies a builder would name. A leaf ends the recursion for that branch.
- **Split one level only.** Put each whole assembly in one child (e.g. `cabin`), not its pieces.
  Children with enough parts are offered to another agent, which decides whether to split them
  further.
- A new submodel needs at least 2 items and can't contain all the items of its section.
- Item ids refer to the section they're listed under. Every id goes to at most one new submodel.
- Write descriptions for the **new** children too. A section's own description should reflect
  what stays in it after the split (its children included, as parts of the whole).

## Reading the digest

- `## S4 "8466 - cab.ldr" -- decide: leaf, or split one level` heads each section. The next lines
  give:
  - its item count, where it's used (`2x in S1`), and its size in studs (1 stud = 20 LDU) and
    bricks (1 brick = 24 LDU, 1 plate = 8 LDU);
  - its path from the root;
  - its current description, which is often just a file name.
- `i12 Technic Beam 7 (black) @40,-8,120` is a part: its catalogue description, colour, and
  position in the section's frame.
  - Coordinates are LDU: x = right, **y = down** (smaller y is higher), z = towards the viewer.
  - Parts with close positions touch or overlap.
- `i13 > S7 "8466 - wheel.ldr" [2 items] "Wheel ..." @60,0,-40` is an existing submodel, placed in
  this section. You can move it into a new child like any part.
- `--- step` marks the author's build-step boundaries. Parts in the same step are often one
  sub-assembly.
- `Flexible ... hose/axle/cable` submodels are flexible parts. They're already isolated; group them
  with the assembly they belong to, if any.

## Example answer

For a car section `S2` with items 0-49 (marked `decide: leaf, or split one level`) and a minifig
`S4` (marked the same):

```json
{"sections": [
  {"id": "S2", "description": "Blue car with UNICEF logo panels, hinged roof, doors and four wheels",
   "new": [
     {"name": "chassis", "items": "0-5,44-49", "description": "Black chassis plates with wheel pin plates and four wheels", "keywords": ["chassis"]},
     {"name": "cabin", "items": "30-40", "description": "Blue cabin with doors, UNICEF logo panels, windscreen and hinged roof", "keywords": ["body"]}],
   "groups": [{"items": "20-23", "comment": "headlights"}]},
  {"id": "S4", "description": "Driver minifig: brown hair, smiling head, blue torso, arms and legs",
   "keywords": ["minifig"], "new": []}
]}
```
