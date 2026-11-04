# Task: annotate a prepped LEGO model (LDraw `.mpd`) yourself

Start from the whole model, prepared as one file (skip this if it's done):

```sh
ldraw-annotator prep <input .ldr/.mpd> --mpd <model.mpd>
```

In `<model.mpd>`:
- every part already has its comment, `0 // <catalogue description> (<colour>)`;
- flexible parts (hoses, cables, chains…) are already in their own submodels, marked
  `0 !KEYWORDS flexible, <kind>`;
- the original `0 FILE` sections (submodels) are all there.

Your job is to edit that file: **structure it into meaningful submodels and describe each one**.
How you work through it (all at once, section by section, top-down…) is up to you.

## Reading the file

- `0 FILE <name>` starts a section. The first one is the main model. Sections named `*.dat` are
  embedded parts.
- `1 <colour> <x> <y> <z> <a b c d e f g h i> <ref>` places a part, or a submodel when `<ref>` is a
  section of the file, in the section's frame.
  - Coordinates are LDU (1 stud = 20, 1 brick = 24, 1 plate = 8): x = right, **y = down** (smaller
    y is higher), z = towards the viewer.
  - Parts with close positions touch or overlap.
- `0 STEP` marks the author's build steps. Parts in the same step are often one sub-assembly.
- An **item** is one part line together with the lines attached right above it (`0 //`,
  `0 BFC INVERTNEXT`, `0 MLCAD BTG`, `0 !LDCAD GROUP_NXT`, `0 !TEXMAP NEXT`), or a whole
  `0 !TEXMAP START … END` block.

## What to do

1. **Describe** every model section: replace the line right after its `0 FILE` with one line of
   plain text (see "Descriptions" below). It must not start with `//`, `!` or a meta command.
2. **Split** sections that hold distinct assemblies, as deep as each branch needs (see "When to
   split"). For each new submodel:
   - add a section, with a name that uses the prefix printed by `prep` (`"prefix"`, e.g. `106 - `):

     ```
     0 STEP
     0 FILE 106 - front-axle.ldr
     0 Front axle with steering links and two wheels
     0 Name: 106 - front-axle.ldr
     0 Author: <copied from the main model, if it has one>
     0 !KEYWORDS steering, wheel
     <the moved items>
     ```

   - **move** its items there **verbatim**, never editing a number of a part line;
   - in the parent, put `1 16 0 0 0 1 0 0 0 1 0 0 0 1 106 - front-axle.ldr` where the items were.
     That reference uses colour 16 and the identity matrix, so the parts keep their exact place and
     colour.
3. Optionally, add a group comment `0 /* headlights */` above a run of items worth naming but not
   worth a submodel.
4. Optionally, rename a section whose name says nothing (`10174_4.ldr`, `house-step36.ldr`,
   `Untitled`) the same way. Update every reference to it and its `0 Name:` line.

## Rules

- Keep the file **lossless**: every part line appears exactly once, with the same colour and the
  same world placement.
- `0 STEP` comes directly before every `0 FILE` except the first. Keep the original `0 STEP` lines
  between the items they separate (they move along with moved items), and remove a step left
  empty.
- Order the model sections parent-first: the main model, then each of its children (in the order
  the parent references them), each followed by its own children, recursively. A shared submodel
  goes where it's first used. Unreferenced sections, flexible parts and embedded `.dat` parts come
  last.
- Don't touch the content of flexible-part sections or embedded `.dat` sections. Move a reference
  to a flexible part along with the assembly it belongs to.

## Finish

Verify your file against the original input, and fix every error until it says `"ok": true`:

```sh
ldraw-annotator check <original input> <model.mpd>
```
