# Route Placement Script

`place_routes.py` places directional route LEDs and regenerates parallel route
silkscreen from construction lines drawn in KiCad.

## Drawing Workflow

1. Use the `Route construction` layer.
2. Draw each route centerline with graphic line segments only. Bends are fine,
   but each segment should be a straight 0/45/90/etc. run. Do not draw arcs on
   the construction layer; the script rounds generated silkscreen bends.
3. Make the segment endpoints touch exactly. Snapping to endpoints/grid is
   recommended.
4. Select all construction segments for one route, group them, and set the
   group name to `route:<route_name>`, for example `route:fauntleroy_vashon`.
5. Add a matching route entry to a JSON config file.

The construction centerline is the midpoint between the two generated
silkscreen lines. With the defaults, the generated silk lines are 4 mm apart,
the two directional LED lines are 4 mm apart, and sequential LED positions are
spaced 5 mm along straight route segments.

## Config Shape

Use `routes.example.json` as a starting point. The simplest route looks like:

```json
{
  "routes": {
    "fauntleroy_vashon": {
      "construction_group": "route:fauntleroy_vashon",
      "refs": "LED526-LED529",
      "endpoints": {
        "start": { "ref": "LED525" },
        "end": { "ref": "LED508" }
      }
    }
  }
}
```

`refs` must be in wiring order. The first half of the refs are placed on the
first direction line from route start to route end. The second half are placed
on the opposite direction line from route end back to route start. This gives a
single data chain of all one-way ferry LEDs followed by all opposite-way ferry
LEDs, with one crossover at the far end. `refs` can be an explicit list, a
range string, or a list that mixes both:

```json
"refs": ["LED526", "LED527", "LED528", "LED529"]
"refs": "LED526-LED529"
"refs": ["LED526-LED529", "LED536", "LED537"]
```

Ranges are inclusive and can use `-` or `..`; `LED526-LED529` and
`LED526..LED529` are equivalent. Ranges can count up or down, so
`LED529-LED526` is valid.
The expanded ref count must be even. By default, the first direction line is on
the left side of the centerline, looking from the route start toward the route
end. Set `"first_direction_side": "right"` on a route to swap that.

Useful per-route overrides:

- `reverse`: reverse the construction-line direction before placing anything.
- `led_spacing_along_mm`: default `5.0`.
- `line_spacing_across_mm`: default `4.0`.
- `silk_parallel_gap_mm`: default `4.0`.
- `silk_fillet_radius_mm`: default `4.0`; generated silkscreen bends are
  rounded to this radius when the adjacent segments are long enough.
- `silk_start_trim_mm` / `silk_end_trim_mm`: shorten generated silk.
- `endpoint_orientation`: default `along_route`; ferry port endpoint LEDs are
  rotated to follow the endpoint route tangent. Use `perpendicular_to_route`
  only for an intentional exception.
- `endpoint_start_rotation_offset_deg`: default `-90.0`; compensates for the
  LED footprint at the start endpoint so its long visual edge follows the route.
- `endpoint_end_rotation_offset_deg`: default `0.0`; compensates for the LED
  footprint at the end endpoint so its long visual edge follows the route.
- `mirror_led_rotations_for_power`: default `true`; rotates one direction line
  by 180 degrees so the +VLED pads face the center power trunk.
- `led_rotation_offset_deg`: rotate route LEDs relative to the segment tangent.
- `strict_led_count`: default `true`; errors if geometry produces a different
  number of positions than the route has LEDs per direction.

Per-segment overrides use zero-based segment indexes in the ordered route:

```json
"segments": {
  "0": { "start_clearance_mm": 2.0 },
  "1": { "no_leds": true },
  "2": { "led_count": 1, "center_offset_along_mm": -0.5 }
}
```

Per-LED overrides are the escape hatch for small visual fixes:

```json
"led_overrides": {
  "LED529": {
    "nudge_mm": [0.2, 0.0],
    "rotation_offset_deg": 180
  }
}
```

## Running

Run with KiCad's bundled Python:

```sh
/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3.9 \
  scripts/place_routes.py \
  --board transit.kicad_pcb \
  --config scripts/routes.json
```

That is a dry run. It prints the ordered segments, computed route LED positions,
endpoint LED positions, and generated silkscreen count.

To write a copy:

```sh
/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3.9 \
  scripts/place_routes.py \
  --board transit.kicad_pcb \
  --config scripts/routes.json \
  --apply \
  --output /tmp/transit-route-test.kicad_pcb
```

To update the project board in place, close the PCB in KiCad first, then run:

```sh
/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3.9 \
  scripts/place_routes.py \
  --board transit.kicad_pcb \
  --config scripts/routes.json \
  --apply
```

Generated silkscreen lines and fillet arcs are grouped as
`routegen:<route_name>`. Rerunning the script deletes and recreates that group,
so manual edits inside generated groups will not be preserved.
