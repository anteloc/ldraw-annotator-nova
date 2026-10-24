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
    - `name`: a short kebab-case name (`front-axle`);
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
