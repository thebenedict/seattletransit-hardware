# Paired Rail LED Placement

This note describes the manual PCB transform used for the Sounder and Tacoma
paired LED rails. It is scriptable because each partner footprint is derived
from its already-placed mate with a fixed geometric rule.

## Inputs

- KiCad board file: `transit.kicad_pcb`
- Sheet path, for example `/Rail/sounder_s/`, `/Rail/sounder_n/`, or
  `/Rail/tacoma_t/`
- First group refs in track order, for example `LED1501` through `LED1512`
- Partner group refs in matching order, for example `LED1513` through
  `LED1524`
- Pair spacing: `4.0 mm`

The schematic should already contain the correct sequential electrical chain
for both groups. If the partner refs are out of order, fix the refs/net chain
first, then apply the geometry transform.

## PCB Parsing

Parse `transit.kicad_pcb` as balanced S-expressions, not line-by-line text.
For this task it is enough to split top-level `(footprint ...)` forms while
respecting quoted strings.

Select only footprints with:

```scheme
(sheetname "/Rail/<sheet_name>/")
```

Then read:

- reference from `(property "Reference" "LED1501" ...)`
- symbol path from `(path ".../<symbol_uuid>")`
- top-level footprint placement from the first footprint-level `(at x y rot)`
- pad nets from pads whose `pinfunction` is `DI_3` or `DO_1`

## Pair Geometry

For each pair:

```text
source_ref  = LED1501 + i
partner_ref = LED1513 + i
```

Read the source footprint center and rotation:

```text
x, y, theta_deg
```

Compute the partner center using the same 4 mm normal offset convention used on
the board:

```text
dx = 4.0 * cos(theta_deg)
dy = -4.0 * sin(theta_deg)

partner_x = x + dx
partner_y = y + dy
partner_rotation = theta_deg + 180
```

Normalize rotation only for presentation if desired. KiCad accepts equivalent
angles such as `-45`, `315`, or `135`; consistency matters more than the exact
range.

Examples:

```text
theta -45  -> offset (+2.828427, +2.828427), partner rotation 135
theta -90  -> offset (0, +4), partner rotation 90
theta -135 -> offset (-2.828427, +2.828427), partner rotation 45
theta 180  -> offset (-4, 0), partner rotation 0
```

This puts the partner footprint on the parallel silkscreen rail and rotates it
to face the opposite travel direction.

## PCB Rewrite

For every partner footprint:

1. Replace the top-level footprint `(at x y rot)` with the computed partner
   placement.
2. Update nested `(at local_x local_y rot)` rotations inside that same
   footprint to the computed partner rotation. This includes reference/value
   fields, user text, and pads.
3. Do not change the footprint UUID, schematic path, reference, pad nets, or
   any unrelated footprint.

The source group footprints are read-only inputs for this operation.

## Sequential Ref Repair

If geometry is correct but partner refs run out of order, derive the actual
chain from the PCB nets:

1. Build a map from each partner footprint's `DI_3` net to its reference.
2. For each partner footprint, map its `DO_1` net to the next footprint whose
   `DI_3` uses that net.
3. Walk from the first partner ref whose `DI_3` is unconnected or externally
   sourced.
4. Rename that walked order to the desired sequential refs.

When applying a ref permutation, use temporary placeholders so names do not
collide:

```text
LED1328 -> __TMP_01__ -> LED1326
```

Apply the same ref permutation to:

- schematic symbol `Reference` properties
- schematic instance references
- PCB footprint `Reference` properties
- PCB net names such as `Net-(LED1328-DO)`
- unconnected net names such as `unconnected-(LED1328-DI-Pad3)`

After renaming, keep symbol UUID/path associations intact. If moving logical
footprints to different physical slots, transplant only the `(at ...)` values
from the destination slot to the logical footprint.

## Validation

After rewriting the PCB, verify all of these before opening KiCad:

1. Every expected source and partner ref exists exactly once on the target
   sheet.
2. For each pair:

   ```text
   hypot(partner_x - source_x, partner_y - source_y) == 4.000000
   (partner_rotation - source_rotation) mod 360 == 180
   ```

3. The partner chain is sequential:

   ```text
   LED1513 -> LED1514 -> ... -> LED1524
   ```

   Derive this from `DO_1` to `DI_3` nets, not from footprint order in the file.

4. PCB footprint paths still match schematic symbol UUIDs for the sheet.
5. Run:

   ```sh
   git diff --check -- transit.kicad_pcb
   ```

## Minimal Script Shape

A future script can expose this as:

```sh
python scripts/place_paired_rail_leds.py \
  --board transit.kicad_pcb \
  --sheet /Rail/tacoma_t/ \
  --source LED1501-LED1512 \
  --partners LED1513-LED1524 \
  --spacing-mm 4.0 \
  --apply
```

The script should default to a dry run that prints the computed placements and
the validation table before writing the board.
