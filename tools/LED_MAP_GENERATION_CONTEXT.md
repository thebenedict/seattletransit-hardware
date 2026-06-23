# Transit Tracker — LED Map Generation: Context & Instructions

This document provides full context for generating `led-maps/rev-a.json`, the board configuration file that maps real-world geographic coordinates to LED indices on a physical transit display board.

Read this document fully before starting work.

## Project Summary

The Transit Tracker is a physical PCB with 247 individually addressable WS2812B LEDs arranged on a stylized transit map of the Puget Sound region. A cloud service polls transit APIs for real-time vehicle positions (lat/lng), maps each position to the nearest LED, and streams color updates to the board over SSE.

The full design document is at `transit-tracker-design.md`. Read it for the SSE protocol, firmware architecture, and web frontend details — but for this task, only the LED map JSON schema (§6) matters.

## The Core Problem

The board's map is **stylized** (like a subway diagram), not geographic. There is no affine transform from lat/lng to board space. We need a data file that lets the server answer: "A vehicle is at lat/lng X on route Y — which LED do I light up?" in effectively constant time.

## The Solution: Route-Progress Based Lookup

Each route is modeled as a progress bar from 0.0 to 1.0 along its real-world geographic polyline:

1. Server receives vehicle position (lat/lng) + route ID + direction from API
2. Project lat/lng onto the route's real-world geoPath polyline → scalar progress (0.0–1.0)
3. Find nearest value in `ledProgress` → LED index

The polyline projection uses standard point-to-polyline snapping: for each segment of the polyline, compute the perpendicular projection, clamp to segment bounds, find the nearest segment, and compute progress as cumulative distance to that point divided by total polyline length. At Puget Sound latitudes (~47.5°N), use a local Cartesian approximation (scale longitude by cos(47.5°)) — Earth curvature error is negligible at city scale.

For the runtime server (Node.js), Turf.js `nearestPointOnLine()` does this in one call. For offline generation (Python), Shapely `line.project(point, normalized=True)` does it.

## Ferry vs Rail: Direction Handling

**Ferry routes** use a single line of LEDs for both directions. A ferry at the midpoint of a crossing lights the same LED regardless of heading. The server ignores direction — it just projects lat/lng onto the route's geoPath. One route object per ferry route.

**Rail routes** have separate LED strips per direction (northbound LEDs are physically different from southbound LEDs on the board). The server uses the trip direction from the API to select the correct route object. Two route objects per rail line (one per direction). Both geoPath polylines follow the same physical track, just in opposite directions.

## Input Data

### 1. Transit_stops__LEDs.csv (in project root)

**This is the primary data source.** A manually created file mapping every LED to its route(s), chain position, and station label. 307 rows, 9 columns:

- **Type**: "Light rail", "Ferry", "Monorail", "Taxi"
- **Chain**: "Rail W", "Rail E", "Ferries N", "Ferries S"  
- **REF**: LED reference designator, e.g. "LED201"
- **Route**: Route identifier — "1" (Line 1), "2" (Line 2), "sea-br" (Seattle-Bremerton), "sea-bi" (Seattle-Bainbridge), "ed-king" (Edmonds-Kingston), "pd-tal" (Point Defiance-Tahlequah), "s-v" (Southworth-Vashon), "f-s" (Fauntleroy-Southworth), "f-v-s" (Fauntleroy-Vashon-Southworth triangle), "taxi-vashon" (Seattle-Vashon water taxi), "taxi-west-seattle" (Seattle-West Seattle water taxi), "monorail"
- **Direction**: "Northbound", "Southbound", "Eastbound", "Westbound", or empty (ferries)
- **Order in route**: 0-indexed position within that route
- **Order in chain**: 0-indexed position within the physical LED chain
- **Label**: Station/terminal name (empty for waypoint LEDs between stations)
- **Note**: Annotations about shared LEDs, skipped designators, etc.

Key structural patterns:
- Station LEDs and waypoint LEDs alternate: every other LED has a label
- LEDs appearing in multiple rows are **shared LEDs** (same physical LED, multiple routes)
- Rail W chain (106 LEDs): LED201-LED253 (northbound) + LED601-LED653 (southbound, separate sub-sheet)
- Rail E chain (48 LEDs): LED301-LED348
- Ferries N chain (46 LEDs): LED401-LED447 (LED430 was skipped due to schematic error)
- Ferries S chain (47 LEDs): LED501-LED547

### 2. GIS Route Polylines (in gis-data/)

These provide the real-world geographic paths for each route:

- **`gis-data/wsdot-ferry-routes.geojson`** — WSF ferry route polylines from WSDOT GIS portal. GeoJSON LineString features with route name attributes. Covers all WSF routes (sea-br, sea-bi, ed-king, pd-tal, s-v, f-s, f-v-s).

- **`gis-data/sound-transit-gtfs/`** — Unzipped Sound Transit GTFS (from `https://www.soundtransit.org/GTFS-rail/40_gtfs.zip`). Contains `shapes.txt` (route polylines), `stops.txt` (station lat/lng), `routes.txt`, `trips.txt`. Covers Link Lines 1 and 2.

- **`gis-data/kcm-gtfs/`** — Unzipped King County Metro GTFS (from `https://www.soundtransit.org/GTFS-KCM/google_transit.zip`). Contains water taxi route shapes and stops. Covers taxi-vashon and taxi-west-seattle.

- **`gis-data/monorail-gtfs/`** — Unzipped Seattle Monorail GTFS (from `https://gtfs.sound.obaweb.org/prod/96_gtfs.zip`). Covers the monorail route.

### 3. Ferry_Routes.geojson (in project root)

May contain the same or similar ferry route data as the WSDOT download. Check and use if present.

## Shared LEDs

These physical LEDs appear in multiple routes. They appear as multiple rows in the CSV with the same REF but different Route values:

1. **LED513** (Southworth) — routes `s-v` and `f-s`
2. **LED525** (Fauntleroy) — routes `f-s` and `f-v-s`  
3. **LED508** (Vashon Island) — routes `s-v`, `f-v-s`, and `taxi-vashon`
4. **LED518** — crossing point where `taxi-vashon` crosses `f-s` (noted "Crosses f-s, shared LED")
5. **LED543** — shared by `taxi-vashon` and `taxi-west-seattle` (Seattle terminal area)
6. **LED601–LED627** on Rail W — shared by Line 1 southbound and Line 2 eastbound (the downtown overlap section, Lynnwood to Intl District)
7. **LED227–LED253** on Rail W — shared by Line 1 northbound and Line 2 westbound

Collision policy: last-writer-wins. When two vehicles on different routes map to the same LED, the most recently processed one determines the color. No blending needed.

## Output: rev-a.json Schema

```jsonc
{
  "boardRevision": "A",
  "totalLeds": 247,
  
  "chains": {
    "rail_w":    { "gpio": 10, "count": 106 },
    "rail_e":    { "gpio": 1,  "count": 48 },
    "ferries_n": { "gpio": 5,  "count": 46 },
    "ferries_s": { "gpio": 4,  "count": 47 }
  },

  "gpios": {
    "vled_en":      { "gpio": 6, "active_high": true },
    "lvl_shift_en": { "gpio": 7, "active_high": false },
    "wifi_led":     { "gpio": 0, "active_high": true }
  },

  "leds": [
    {
      "index": 0,                    // global LED index (0..246)
      "chain": "rail_w",             // which physical chain
      "chainIndex": 0,               // position within chain (0-indexed)
      "ref": "LED201",               // schematic reference designator
      "label": "Federal Way Downtown", // station name, or null for waypoints
      "type": "station",             // "station" | "terminal" | "waypoint"
      "package": "2020"              // "2020" (WS2812B-2020) or "mini" (WS2812B-MINI)
    }
    // ... 247 entries total
  ],

  "routes": [
    {
      "id": "link-1-northbound",
      "type": "rail",                // "rail" | "ferry" | "monorail" | "water_taxi"
      "operator": "sound_transit",
      "line": "1",                   // matches Route column in CSV
      "direction": "northbound",     // null for ferries
      "defaultColor": { "r": 0, "g": 200, "b": 0 },

      // Ordered LED indices for this route (references into leds[])
      "ledIndices": [0, 1, 2, ...],

      // Real-world route polyline (from GTFS/GIS)
      "geoPath": [[47.3174, -122.3032], [47.3180, -122.3030], ...],

      // Progress value (0.0-1.0) along geoPath for each LED
      // Same length as ledIndices
      "ledProgress": [0.0, 0.019, 0.038, ...]
    }
  ],

  "sharedLeds": [
    {
      "ledIndex": 42,               // global index
      "ref": "LED513",
      "routes": ["southworth-vashon", "fauntleroy-southworth"],
      "note": "Southworth terminal"
    }
  ],

  "collisionPolicy": "last_writer_wins"
}
```

### Important notes about the schema:

- `leds[].index` is the global index (0-246). The mapping from global index to chain is deterministic: rail_w = 0..105, rail_e = 106..153, ferries_n = 154..199, ferries_s = 200..246. The `chainIndex` field gives the position within the chain.

- `leds[]` does not contain geographic coordinates. All geographic data lives in `routes[].geoPath` and `routes[].ledProgress`. The leds array is purely about physical board identity (chain, index, ref, label, package).

- `routes[].geoPath` is `[lat, lng]` pairs (not `[lng, lat]` — match the GTFS convention).

- `routes[].ledIndices` references global LED indices, not chain indices. A route can span chains (Line 2 spans rail_w and rail_e).

- `routes[].ledProgress` — for station LEDs, computed by projecting the GTFS stop coordinate onto the route's geoPath. For waypoint LEDs (unlabeled, between stations), interpolated evenly between the flanking stations' progress values.

- At runtime, the server finds the nearest LED by binary-searching `ledProgress` for the vehicle's progress value, then comparing the two neighboring entries to find the closest. No precomputed thresholds needed.

- Ferry routes have `"direction": null`. Rail routes always have a direction.

- The `package` field: the 13 ferry terminal LEDs use "mini" (WS2812B-MINI, larger package). All others use "2020". The terminals are the labeled endpoints of ferry routes (Seattle, Bainbridge Island, Bremerton, Kingston, Edmonds, Fauntleroy, Vashon Island, Southworth, Point Defiance, Tahlequah, West Seattle) plus the two water taxi Seattle terminals. Determine which are "mini" from the CSV labels at chain endpoints.

## What to Build

A Python script (or small set of scripts) that:

1. **Parses the CSV** to extract chain order, route definitions, station labels, and shared LED relationships.

2. **Reads GIS data** (GeoJSON for ferries, GTFS shapes.txt/stops.txt for rail/monorail/water taxi) to get route polylines and station coordinates.

3. **Matches stations to GTFS stops** — the CSV `Label` values need to be matched to GTFS `stop_name` values to get lat/lng. This may require fuzzy matching (e.g., "SeaTac / Airport" vs "SeaTac/Airport Station"). Log any unmatched stations for manual review.

4. **Builds geoPath for each route** — for rail, extract the GTFS shape polyline for the correct route+direction. For ferries, extract the matching route from the ferry GeoJSON. For routes spanning multiple chains (Line 2), concatenate the geoPath segments.

5. **Computes ledProgress** — project each station LED's GTFS stop coordinate onto its route's geoPath to get its progress value. Interpolate waypoint LEDs evenly between flanking stations.

6. **Assembles and writes rev-a.json** — combining all the above into the schema described.

7. **Validates** — check LED count (247), chain sizes (106, 48, 46, 47), route continuity, progress monotonicity, geographic plausibility (station lat/lng from GTFS within Puget Sound bounding box ~47.0-48.0 lat, -123.0 to -122.0 lng).

### Handling GTFS shapes.txt

GTFS `shapes.txt` format:
```
shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence,shape_dist_traveled
```

To find the right shape_id for a given route+direction:
1. In `routes.txt`, find the `route_id` for the line (filter by `route_short_name` or `route_long_name`)
2. In `trips.txt`, find trips for that `route_id` with the desired `direction_id` (0 or 1)
3. Get the `shape_id` from those trips
4. In `shapes.txt`, collect all points for that shape_id, ordered by sequence
5. Convert to a GeoJSON LineString or list of [lat, lng] pairs

### Handling ferry GeoJSON

The WSDOT ferry routes GeoJSON has features with route identification in their properties (likely a `ROUTE_ID` or `ROUTENAME` field). Inspect the file to determine the exact property name, then filter features by route. Each feature's geometry is a LineString of [lng, lat] coordinates (GeoJSON standard is [lng, lat], so swap to [lat, lng] for our schema).

### Route ID Mapping

Map the CSV `Route` values to the GIS data source identifiers:

| CSV Route | Type | GIS Source | Notes |
|-----------|------|-----------|-------|
| 1 | rail | Sound Transit GTFS | Line 1, both directions |
| 2 | rail | Sound Transit GTFS | Line 2, both directions |
| sea-br | ferry | WSDOT GeoJSON | Seattle-Bremerton |
| sea-bi | ferry | WSDOT GeoJSON | Seattle-Bainbridge |
| ed-king | ferry | WSDOT GeoJSON | Edmonds-Kingston |
| pd-tal | ferry | WSDOT GeoJSON | Point Defiance-Tahlequah |
| s-v | ferry | WSDOT GeoJSON | Southworth-Vashon |
| f-s | ferry | WSDOT GeoJSON | Fauntleroy-Southworth |
| f-v-s | ferry | WSDOT GeoJSON | Fauntleroy-Vashon (triangle leg) |
| taxi-vashon | water_taxi | KCM GTFS | Seattle-Vashon water taxi |
| taxi-west-seattle | water_taxi | KCM GTFS | Seattle-West Seattle water taxi |
| monorail | monorail | Monorail GTFS | Westlake-Seattle Center |

### Color Defaults

| Route type | Color |
|-----------|-------|
| Line 1 | Green: (0, 200, 0) |
| Line 2 | Blue: (0, 80, 255) |
| WSF ferries | White: (255, 255, 255) |
| Water taxis | Turquoise: (0, 220, 200) |
| Monorail | Red: (255, 0, 0) |

## Working Notes

- The CSV has 307 rows but only 247 unique LEDs — the extra rows are shared LEDs appearing in multiple routes.
- LED430 was skipped in the schematic (noted in CSV). The chain goes LED429 → LED431. This doesn't affect anything — just means there's no LED with ref "LED430".
- The `Order in chain` column in the CSV directly gives the `chainIndex` value for each LED.
- The `Order in route` column gives the position within the route's `ledIndices` array.
- For Line 2, the route spans two chains: it starts on Rail W (shared LEDs) then continues on Rail E (or vice versa depending on direction). The `ledIndices` array will contain global indices from both chains.