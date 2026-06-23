#!/usr/bin/env python3
"""
Generate led-maps/rev-a.json from:
  - transit_stop_leds.csv
  - gis-data/polylines/*.geojson

Each route has one canonical polyline (from gis-data/polylines/). For the
opposite direction of a bidirectional route, the polyline is reversed so that
ledProgress is always monotonically increasing.
"""

import csv
import json
import math
import os
from collections import defaultdict

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHAIN_OFFSETS = {
    "Rail W":    ("rail_w",    0),
    "Rail E":    ("rail_e",    106),
    "Ferries N": ("ferries_n", 154),
    "Ferries S": ("ferries_s", 200),
}

CHAIN_DEFS = {
    "rail_w":    {"gpio": 10, "count": 106},
    "rail_e":    {"gpio": 1,  "count": 48},
    "ferries_n": {"gpio": 5,  "count": 46},
    "ferries_s": {"gpio": 4,  "count": 47},
}

GPIO_DEFS = {
    "vled_en":      {"gpio": 6, "active_high": True},
    "lvl_shift_en": {"gpio": 7, "active_high": False},
    "wifi_led":     {"gpio": 0, "active_high": True},
}

ROUTE_POLYLINE_FILE = {
    "1":                 "link-1",
    "2":                 "link-2",
    "sea-br":            "sea-br",
    "sea-bi":            "sea-bi",
    "ed-king":           "ed-king",
    "pd-tal":            "pd-tal",
    "s-v":               "s-v",
    "f-s":               "f-s",
    "f-v-s":             "f-v-s",
    "taxi-vashon":       "taxi-vashon",
    "taxi-west-seattle": "taxi-west-seattle",
    "monorail":          "monorail",
}

ROUTE_TYPE = {
    "1": "rail", "2": "rail",
    "sea-br": "ferry", "sea-bi": "ferry", "ed-king": "ferry",
    "pd-tal": "ferry", "s-v": "ferry", "f-s": "ferry", "f-v-s": "ferry",
    "taxi-vashon": "water_taxi", "taxi-west-seattle": "water_taxi",
    "monorail": "monorail",
}

ROUTE_OPERATOR = {
    "1": "sound_transit", "2": "sound_transit",
    "sea-br": "wsf", "sea-bi": "wsf", "ed-king": "wsf",
    "pd-tal": "wsf", "s-v": "wsf", "f-s": "wsf", "f-v-s": "wsf",
    "taxi-vashon": "king_county_metro", "taxi-west-seattle": "king_county_metro",
    "monorail": "seattle_monorail",
}

ROUTE_DEFAULT_COLOR = {
    "1":                 {"r": 0,   "g": 200, "b": 0},
    "2":                 {"r": 0,   "g": 80,  "b": 255},
    "sea-br":            {"r": 255, "g": 255, "b": 255},
    "sea-bi":            {"r": 255, "g": 255, "b": 255},
    "ed-king":           {"r": 255, "g": 255, "b": 255},
    "pd-tal":            {"r": 255, "g": 255, "b": 255},
    "s-v":               {"r": 255, "g": 255, "b": 255},
    "f-s":               {"r": 255, "g": 255, "b": 255},
    "f-v-s":             {"r": 255, "g": 255, "b": 255},
    "taxi-vashon":       {"r": 0,   "g": 220, "b": 200},
    "taxi-west-seattle": {"r": 0,   "g": 220, "b": 200},
    "monorail":          {"r": 255, "g": 0,   "b": 0},
}

DIRECTION_MAP = {
    "Northbound": "northbound",
    "Southbound": "southbound",
    "Eastbound":  "eastbound",
    "Westbound":  "westbound",
    "":           None,
}

# Local Cartesian approximation at Puget Sound latitude
COS_LAT = math.cos(math.radians(47.5))


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def project_onto_polyline(lat, lng, polyline):
    """
    Project (lat, lng) onto a polyline and return normalized progress [0, 1].
    polyline is a list of (lat, lng) tuples.
    Uses a local Cartesian approximation (lng scaled by cos(47.5°)).
    """
    px, py = lng * COS_LAT, lat

    total_len = 0.0
    seg_lens = []
    for i in range(len(polyline) - 1):
        la, lo = polyline[i]
        lb, ro = polyline[i + 1]
        dx = (ro - lo) * COS_LAT
        dy = lb - la
        seg_len = math.sqrt(dx * dx + dy * dy)
        seg_lens.append(seg_len)
        total_len += seg_len

    if total_len == 0:
        return 0.0

    best_dist_sq = float("inf")
    best_progress = 0.0
    cumulative = 0.0

    for i, seg_len in enumerate(seg_lens):
        la, lo = polyline[i]
        lb, ro = polyline[i + 1]
        ax, ay = lo * COS_LAT, la
        bx, by = ro * COS_LAT, lb
        dx, dy = bx - ax, by - ay
        len_sq = dx * dx + dy * dy

        t = 0.0 if len_sq < 1e-20 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / len_sq))
        cx, cy = ax + t * dx, ay + t * dy
        dist_sq = (px - cx) ** 2 + (py - cy) ** 2

        if dist_sq < best_dist_sq:
            best_dist_sq = dist_sq
            best_progress = (cumulative + t * seg_len) / total_len

        cumulative += seg_len

    return best_progress


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # -----------------------------------------------------------------------
    # 1. Parse CSV
    # -----------------------------------------------------------------------
    with open("transit_stop_leds.csv") as f:
        rows = list(csv.DictReader(f))

    # -----------------------------------------------------------------------
    # 2. Build per-LED metadata (unique by REF)
    # -----------------------------------------------------------------------
    led_by_ref = {}
    for row in rows:
        ref = row["REF"]
        if ref not in led_by_ref:
            led_by_ref[ref] = {
                "ref":         ref,
                "chain":       row["Chain"],
                "chain_index": int(row["Order in chain"]),
                "label":       row["Label"].strip() or None,
                "type_csv":    row["Type"],
                "appearances": [],
            }
        led_by_ref[ref]["appearances"].append({
            "route":     row["Route"],
            "direction": row["Direction"].strip(),
            "order":     int(row["Order in route"]),
            "lat":       float(row["Lat"]) if row["Lat"].strip() else None,
            "lng":       float(row["Lng"]) if row["Lng"].strip() else None,
        })

    # -----------------------------------------------------------------------
    # 3. Classify LED type: terminal / station / waypoint
    #    terminal = labeled + is order 0 or max-order in at least one route
    #    station  = labeled + intermediate in every route it appears in
    #    waypoint = no label
    # -----------------------------------------------------------------------
    route_max_order = defaultdict(int)
    for row in rows:
        key = (row["Route"], row["Direction"].strip())
        route_max_order[key] = max(route_max_order[key], int(row["Order in route"]))

    terminal_refs = set()
    for ref, led in led_by_ref.items():
        if not led["label"]:
            continue
        for app in led["appearances"]:
            key = (app["route"], app["direction"])
            if app["order"] == 0 or app["order"] == route_max_order[key]:
                terminal_refs.add(ref)
                break

    # mini package = labeled terminal of a ferry or water_taxi route
    mini_refs = set()
    for ref in terminal_refs:
        for app in led_by_ref[ref]["appearances"]:
            if ROUTE_TYPE.get(app["route"]) in ("ferry", "water_taxi"):
                mini_refs.add(ref)
                break

    # -----------------------------------------------------------------------
    # 4. Assign global indices and build leds[]
    # -----------------------------------------------------------------------
    # Sort by (chain_offset + chain_index) to get deterministic global ordering
    def sort_key(item):
        _, chain_offset = CHAIN_OFFSETS[item[1]["chain"]]
        return chain_offset + item[1]["chain_index"]

    leds_list = []
    ref_to_global = {}

    for ref, led in sorted(led_by_ref.items(), key=sort_key):
        chain_name, chain_offset = CHAIN_OFFSETS[led["chain"]]
        global_index = chain_offset + led["chain_index"]

        if not led["label"]:
            led_type = "waypoint"
        elif ref in terminal_refs:
            led_type = "terminal"
        else:
            led_type = "station"

        leds_list.append({
            "index":      global_index,
            "chain":      chain_name,
            "chainIndex": led["chain_index"],
            "ref":        ref,
            "label":      led["label"],
            "type":       led_type,
            "package":    "mini" if ref in mini_refs else "2020",
        })
        ref_to_global[ref] = global_index

    # -----------------------------------------------------------------------
    # 5. Build routes[]
    # -----------------------------------------------------------------------

    # Group CSV rows by (route_key, direction_str), preserve order
    route_groups = defaultdict(list)
    for row in rows:
        key = (row["Route"], row["Direction"].strip())
        route_groups[key].append(row)
    for key in route_groups:
        route_groups[key].sort(key=lambda r: int(r["Order in route"]))

    def load_polyline(route_key):
        """Load polyline as list of (lat, lng) from GeoJSON [lng, lat] file."""
        path = f"gis-data/polylines/{ROUTE_POLYLINE_FILE[route_key]}.geojson"
        with open(path) as f:
            coords = json.load(f)["features"][0]["geometry"]["coordinates"]
        return [(lat, lng) for lng, lat in coords]

    routes_list = []

    for (route_key, direction_str), route_rows in sorted(route_groups.items()):
        polyline = load_polyline(route_key)

        # Project all station LEDs in this route onto the polyline
        station_progress = {}  # order → progress
        for row in route_rows:
            lat = float(row["Lat"]) if row["Lat"].strip() else None
            lng = float(row["Lng"]) if row["Lng"].strip() else None
            if lat is not None and lng is not None:
                order = int(row["Order in route"])
                station_progress[order] = project_onto_polyline(lat, lng, polyline)

        # If progress decreases along the route, reverse the polyline so that
        # ledProgress is monotonically increasing (required for binary search)
        if len(station_progress) >= 2:
            sorted_orders = sorted(station_progress)
            first_prog = station_progress[sorted_orders[0]]
            last_prog  = station_progress[sorted_orders[-1]]
            if first_prog > last_prog:
                polyline = list(reversed(polyline))
                station_progress = {
                    order: project_onto_polyline(
                        float(row["Lat"]), float(row["Lng"]), polyline
                    )
                    for row in route_rows
                    if row["Lat"].strip()
                    for order in [int(row["Order in route"])]
                }

        # Build interpolated progress for every LED in the route
        sorted_anchors = sorted(station_progress.items())  # [(order, progress), ...]

        all_orders = [int(r["Order in route"]) for r in route_rows]
        min_order, max_order = min(all_orders), max(all_orders)

        # Extend anchors to route endpoints if not already covered
        if not sorted_anchors or sorted_anchors[0][0] > min_order:
            sorted_anchors.insert(0, (min_order, 0.0))
        if sorted_anchors[-1][0] < max_order:
            sorted_anchors.append((max_order, 1.0))

        def interpolate_progress(order):
            # Direct hit
            for anchor_order, anchor_prog in sorted_anchors:
                if anchor_order == order:
                    return anchor_prog
            # Find flanking anchors
            prev = next((a for a in reversed(sorted_anchors) if a[0] < order), None)
            nxt  = next((a for a in sorted_anchors           if a[0] > order), None)
            if prev is None:
                return nxt[1]
            if nxt is None:
                return prev[1]
            t = (order - prev[0]) / (nxt[0] - prev[0])
            return prev[1] + t * (nxt[1] - prev[1])

        led_indices = []
        led_progress_values = []
        for row in route_rows:
            order = int(row["Order in route"])
            led_indices.append(ref_to_global[row["REF"]])
            led_progress_values.append(round(interpolate_progress(order), 6))

        # Route ID and direction
        direction = DIRECTION_MAP.get(direction_str, direction_str.lower() or None)
        route_id = f"{route_key}-{direction}" if direction else route_key

        # geoPath stored as [lat, lng] per schema convention
        geo_path = [[lat, lng] for lat, lng in polyline]

        route_obj = {
            "id":           route_id,
            "type":         ROUTE_TYPE[route_key],
            "operator":     ROUTE_OPERATOR[route_key],
            "line":         route_key,
            "direction":    direction,
            "defaultColor": ROUTE_DEFAULT_COLOR[route_key],
            "ledIndices":   led_indices,
            "geoPath":      geo_path,
            "ledProgress":  led_progress_values,
        }
        routes_list.append(route_obj)

    # -----------------------------------------------------------------------
    # 6. Build sharedLeds[]
    # -----------------------------------------------------------------------
    shared_leds = []
    for ref, led in led_by_ref.items():
        if len(led["appearances"]) > 1:
            route_ids = []
            for app in led["appearances"]:
                direction = DIRECTION_MAP.get(app["direction"], None)
                rid = f"{app['route']}-{direction}" if direction else app["route"]
                route_ids.append(rid)
            entry = {
                "ledIndex": ref_to_global[ref],
                "ref":      ref,
                "routes":   route_ids,
            }
            if led["label"]:
                entry["note"] = led["label"]
            shared_leds.append(entry)
    shared_leds.sort(key=lambda x: x["ledIndex"])

    # -----------------------------------------------------------------------
    # 7. Assemble and write output
    # -----------------------------------------------------------------------
    output = {
        "boardRevision":   "A",
        "totalLeds":       247,
        "chains":          CHAIN_DEFS,
        "gpios":           GPIO_DEFS,
        "leds":            leds_list,
        "routes":          routes_list,
        "sharedLeds":      shared_leds,
        "collisionPolicy": "last_writer_wins",
    }

    os.makedirs("led-maps", exist_ok=True)
    out_path = "led-maps/rev-a.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    # -----------------------------------------------------------------------
    # 8. Validate
    # -----------------------------------------------------------------------
    errors = []

    if len(leds_list) != 247:
        errors.append(f"LED count: expected 247, got {len(leds_list)}")

    chain_counts = defaultdict(int)
    for led in leds_list:
        chain_counts[led["chain"]] += 1
    for chain, expected in [("rail_w", 106), ("rail_e", 48), ("ferries_n", 46), ("ferries_s", 47)]:
        if chain_counts[chain] != expected:
            errors.append(f"Chain {chain}: expected {expected}, got {chain_counts[chain]}")

    puget_sound_bbox = (47.0, 48.0, -123.0, -122.0)
    for route in routes_list:
        prog = route["ledProgress"]
        for i in range(len(prog) - 1):
            if prog[i + 1] < prog[i] - 0.001:
                errors.append(f"Non-monotonic ledProgress in {route['id']} at index {i}: {prog[i]:.4f} → {prog[i+1]:.4f}")
                break
        for lat, lng in route["geoPath"]:
            if not (puget_sound_bbox[0] <= lat <= puget_sound_bbox[1] and
                    puget_sound_bbox[2] <= lng <= puget_sound_bbox[3]):
                errors.append(f"Out-of-bounds coord in {route['id']}: [{lat}, {lng}]")
                break

    mini_count = sum(1 for led in leds_list if led["package"] == "mini")
    if mini_count != 13:
        errors.append(f"Expected 13 mini LEDs, got {mini_count}")

    # Summary
    print(f"Wrote {out_path}")
    print(f"  {len(leds_list)} LEDs  ({sum(1 for l in leds_list if l['type']=='terminal')} terminals, "
          f"{sum(1 for l in leds_list if l['type']=='station')} stations, "
          f"{sum(1 for l in leds_list if l['type']=='waypoint')} waypoints)")
    print(f"  {len(routes_list)} routes")
    print(f"  {len(shared_leds)} shared LEDs")
    print(f"  {mini_count} mini-package LEDs")

    if errors:
        print("\nVALIDATION ERRORS:")
        for e in errors:
            print(f"  ✗ {e}")
    else:
        print("\nAll validation checks passed.")

    # Per-route summary
    print("\nRoutes:")
    for r in routes_list:
        prog = r["ledProgress"]
        print(f"  {r['id']:35s}  {len(r['ledIndices']):3d} LEDs  "
              f"progress [{prog[0]:.3f} → {prog[-1]:.3f}]")


if __name__ == "__main__":
    main()
