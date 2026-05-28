# Route Placement Scripts

## One-Line Route Placer

`place_route.py` places one line of WS2812B-2020 point LEDs along an existing
silkscreen route. On apply, it rewrites that silkscreen group with filleted
bends. It does not move WS2812B-MINI ferryport LEDs.

Draw the route as F.Silkscreen graphic line segments, group the segments for
one route, and name the group `route:<route_name>`, for example
`route:mukilteo_clinton`. A single straight route can be one grouped line
segment. For this first pass, draw source paths with line segments. After the
script fillets a group, reruns can still read the filleted line/arc pieces for
placement and will leave the existing filleted silk unchanged by default.

Routes can specify either explicit refs or a schematic sheet name:

```json
{
  "routes": {
    "mukilteo_clinton": {
      "silkscreen_group": "route:mukilteo_clinton",
      "schematic": "mukilteo_clinton"
    },
    "example_by_refs": {
      "silkscreen_group": "route:example_by_refs",
      "refs": "LED509-LED512"
    }
  }
}
```

When you pass `--route <route_name>` and that route is not present in the
config, `place_route.py` uses the same simple convention by default:
`silkscreen_group` becomes `route:<route_name>` and `schematic` becomes
`<route_name>`. This lets a config file act as defaults plus a few route
overrides.

When `schematic` is used, the script places all WS2812B-2020 point LED
footprints whose board sheet file or sheet name matches that value. MINI
ferryport LEDs in that schematic are skipped.

Run a dry run with KiCad's bundled Python:

```sh
/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3.9 \
  scripts/place_route.py \
  --board transit.kicad_pcb \
  --config scripts/place_route.example.json \
  --route mukilteo_clinton
```

Add `--apply` to write the board. `silk_fillet_radius_mm` defaults to `4.0`;
set it per route or in `defaults`, or set it to `0` for sharp corners.
`silk_width_mm` defaults to `0.2`. Set `generate_silk: false` if you want a
route run to leave the grouped silkscreen untouched. If you intentionally want
to rewrite an already-filleted group, set `refillet_existing_silk: true`, but
the cleanest source remains straight segments. Use `reverse_path: true` when
the computed placement order is opposite the route direction you want, and
`reverse_refs: true` when the footprint order should run the other way along
the same path. For one-off runs, the same settings can be passed as
`--reverse-path` / `--no-reverse-path` and `--reverse-refs` /
`--no-reverse-refs`; CLI values override the config for the selected routes.

## Directional Two-Line Route Placer

`place_routes.py` places directional route LEDs and regenerates parallel route
silkscreen from construction lines drawn in KiCad.

## Station Decoration Generator

`stationgen` generates decorative station silkscreen from
`scripts/station_decorations.yaml`. It is intentionally scoped to generated
graphics: standard plain labels, rounded transfer rectangles, and terminal
knockout label pills. It does not move or own LED footprints.

Use the `Capture Selected Station to Config` KiCad IPC action to add stations
from selected LED footprints without typing reference designators. The visible
KiCad menu entry is a small legacy Action Plugin launcher that delegates to the
IPC implementation. See
`docs/stationgen.md` for the KiCad IPC plugin setup, capture workflow, and
config shape.

## Drawing Workflow

1. Use the `Route construction` layer.
2. Draw each route centerline with graphic line segments only. Bends are fine,
   but each segment should be a straight 0/45/90/etc. run. Do not draw arcs on
   the construction layer; the script rounds generated silkscreen bends.
3. Make the segment endpoints touch exactly. Snapping to endpoints/grid is
   recommended.
4. Select all construction segments for one route, group them, and set the
   group name to `route:<route_name>`, for example `route:fauntleroy_vashon`.
5. For a single straight route, duplicate the construction line in place, group
   the two collinear lines, and give that group the route name. The script
   merges overlapping collinear construction lines back into one segment.
6. Add a matching route entry to a JSON config file.

The construction centerline is the midpoint between the two generated
silkscreen lines. With the defaults, the generated silk lines are 4 mm apart,
the two directional LED lines are 4 mm apart, and sequential LED positions are
spaced 5 mm along straight route segments.

Construction endpoints are allowed to be ferry port LED anchors. Non-port ferry
LEDs and generated route silk use the coast-trimmed route path instead. Use
`coast_start_trim_mm` and `coast_end_trim_mm` when the construction line extends
past the coastline to reach a port LED.

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

`endpoints` can contain one endpoint. Use only `start` or only `end` when a
route reuses a port LED that is owned by another route.

Useful per-route overrides:

- `reverse`: default `true`; the script reverses the ordered construction
  polyline before placing anything. Set to `false` for a route whose
  construction traversal already runs from electrical start to electrical end.
- `coast_start_trim_mm` / `coast_end_trim_mm`: trim the construction path back
  to the coastline before placing non-port ferry LEDs or generating silk.
- `coast_clearance_mm`: default `0.0`; clearance from the coast-trimmed route
  ends to the first non-port ferry LED positions. Endpoint port LEDs ignore this
  and stay anchored to construction endpoints.
- `segment_clearance_mm`: default `0.0`; optional clearance at both ends of
  every straight construction segment. Use this only when you want extra spacing
  near bends as well as coasts.
- `merge_collinear_segments`: default `true`; overlapping or touching collinear
  construction lines are treated as one logical segment. Set to `false` when a
  special route needs to address those drawn subsegments by index.
- `led_spacing_along_mm`: default `5.0`.
- `line_spacing_across_mm`: default `4.0`.
- `silk_parallel_gap_mm`: default `4.0`.
- `silk_fillet_radius_mm`: default `4.0`; generated silkscreen bends are
  rounded to this radius when the adjacent segments are long enough.
- `silk_start_trim_mm` / `silk_end_trim_mm`: additionally shorten generated
  silk after coast trimming.
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
  number of positions than the route has LEDs per direction. Set to `false` for
  best-effort placement: extra positions are ignored, and too few positions
  compress the route spacing so every ref still gets placed.

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

To regenerate only a route's generated silkscreen while leaving all LEDs and
reference visibility alone:

```sh
/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3.9 \
  scripts/place_routes.py \
  --board transit.kicad_pcb \
  --config scripts/routes.json \
  --route mukilteo_clinton \
  --apply \
  --silk-only
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
