# Task: rewrite parent descriptions bottom-up

You get the submodel tree of one LEGO model (LDraw format). Each line shows:
- a section id;
- its name;
- how many times it's used (`x4`);
- its number of direct items;
- its current description.

Children are indented under their parent.

For each section listed at the end ("Rewrite the descriptions of: ..."), write a new one-line
description that summarises what the section is, using what its children's descriptions reveal:
- The root describes the whole model: what it is, its main assemblies, notable colours and features.
- A parent describes the assembly as a whole, its role in its own parent, and its main sub-assemblies.

Style:
- One line, at most ~30 words, plain text.
- Describe what a person looking at it sees: shape, colours, distinctive elements, the role it plays.
- Name the subject where it helps ("rear suspension of the Lamborghini Sián"), but not in every
  line.
- No part numbers, no item counts, no invented mechanics. Only claim a function (e.g. "steers the
  front wheels") when the parts make it evident.

Return every listed section with its new description.
