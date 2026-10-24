# Annotation guide (knowledge base for agents)

The annotated models are read by tools and agents that **learn from them to generate new models**
and that **animate their mechanisms**. A good hierarchy names the assemblies a builder would
name, and isolates things that move.

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

## When to split (and when not to)

Split a section when it holds **distinct assemblies that a builder would name**:
- **Things that move relative to the rest:**
  - wheels/axles (wheel = rim + tyre);
  - doors and hatches with their hinges;
  - turntables;
  - steering;
  - gear trains, gearboxes and differentials;
  - pistons and engines;
  - cranes, booms, arms, landing gear, propellers and rotors.

  Animation needs these as separate submodels.
- **Functional or structural units:**
  - chassis/frame;
  - body/hull and cabin/cockpit;
  - roof and walls;
  - floors and rooms of a building;
  - interior and furniture;
  - base/baseplate and scenery;
  - weapons and cargo.
- **Each minifigure** (head + torso + arms + hands + hips + legs + accessories) is its own submodel.
  So is each animal or creature.
- **Repeated units** each get their own child: 4 wheels can be 4 children (or 1-2 axle children
  holding them), and 2 identical wings are 2 children.
- **Hinged parts count even when small.** A door, shutter, hatch, flap or lid on a hinge is its own
  child, together with its moving half of the hinge.
- **In a big section, look at all of it.** After the obvious assemblies (wheels, minifigs,
  mechanisms), the remaining structure often holds named units of its own: chassis/frame, body
  shell, cockpit, walls, floor, roof. Name them when they're real units. Leave parts in the parent
  when they're what holds the children together.

Answer **leaf** (`"new": []`) when:
- the section is one cohesive unit (a wheel, a minifig, a small mechanism, a single wall);
- the only possible groupings are arbitrary (splitting a solid wall into "bottom bricks" and "top
  bricks");
- groups would be single parts or tiny fragments.

Never split just to reach a count or to make a deeper tree.

No single clue is enough; combine them:
- part descriptions (Minifig*, Wheel, Tyre, Gear, Hinge, Door, Turntable, Steering, Axle, Window…);
- build steps;
- positions and sizes;
- colours;
- the names of existing submodels.

## Names, keywords, group comments

- Names are short kebab-case words: `front-axle`, `rear-suspension`, `cabin`, `minifig-pilot`,
  `ground-floor`, `crane-boom`. The tool adds the model prefix and `.ldr`.
- Prefer the words other tools search for:
  - mechanisms: steering, suspension, gearbox, differential, drivetrain, engine, piston, crank,
    turntable, winch, boom, hinge, door, wheel;
  - structure: chassis, frame, mount, support.
- Keywords (fixed list) tag function, e.g. `["wheel"]` or `["steering"]`. Leave them empty when
  nothing fits.
- Group comments name a contiguous run of items that's meaningful but not worth a submodel, for
  example "radiator grille", "left wall windows" or "mounting pins".

## Descriptions

- One line, at most ~25 words, plain text. Describe what a person looking at it sees: shape,
  colours, distinctive elements, and its role in the parent.
- Name the subject where it helps ("Cockpit of the red Ferrari F40", "Driver minifig").
- No part numbers and no item counts. Don't invent mechanics: claim a function only when the parts
  make it evident (a gear rack + steering links = steering rack).

Good examples:
- `Wheel assembly: light grey rim with black offset-tread tyre`
- `Driver minifig: brown hair, smiling yellow head, blue torso with patch pocket, blue arms, hips and legs, yellow hands`
- `Blue car body with UNICEF logo panels, windscreen, hinged roof, doors, headlight bricks, steering wheel, radio, shovel, and four wheels`
- `Steering rack mechanism using a gear rack, steering links, and towball connectors`
- `Front axle assembly with an open-center beam frame, steering linkage gears, and wheel hubs`
- `Cockpit assembly with a large clear curved windscreen, domed round corner bricks, and a dish forming a canopy`
- `Roof section of white and dark-blue plates with curved slopes and a skylight dish`
- `Rear wheel and axle assembly with two dark red wheel rims, long liftarms, and bushings`

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
