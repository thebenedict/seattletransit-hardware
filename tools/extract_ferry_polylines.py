#!/usr/bin/env python3
"""
Extract ferry and water taxi route polylines from wsdot_ferry_routes.geojson
and write one GeoJSON file per route to gis-data/polylines/.
"""

import json
import os

INPUT = "gis-data/wsdot_ferry_routes.geojson"
OUTPUT_DIR = "gis-data/polylines"

# Map CSV route ID -> Display name in the WSDOT GeoJSON
ROUTE_MAP = {
    "sea-br":            "Seattle to Bremerton",
    "sea-bi":            "Seattle to Bainbridge Island",
    "ed-king":           "Edmonds to Kingston",
    "pd-tal":            "Point Defiance to Tahlequah",
    "s-v":               "Southworth to Vashon",
    "f-s":               "Fauntleroy to Southworth",
    "f-v-s":             "Fauntleroy to Vashon",
    "taxi-vashon":       "Seattle to Vashon",
    "taxi-west-seattle": "Downtown Seattle to West Seattle",
}

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    with open(INPUT) as f:
        data = json.load(f)

    # Index features by Display name
    by_display = {}
    for feat in data["features"]:
        display = feat["properties"].get("Display", "").strip()
        by_display[display] = feat

    for route_id, display_name in ROUTE_MAP.items():
        feat = by_display.get(display_name)
        if feat is None:
            print(f"WARNING: no feature found for '{display_name}' (route {route_id})")
            continue

        geom = feat["geometry"]
        if geom["type"] != "LineString":
            print(f"WARNING: expected LineString for {route_id}, got {geom['type']}")
            continue

        output = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "route_id": route_id,
                        "display": display_name,
                        "source": "wsdot_ferry_routes.geojson",
                        "objectid": feat["properties"].get("OBJECTID"),
                        "owner": feat["properties"].get("Owner"),
                    },
                    "geometry": {
                        "type": "LineString",
                        # GeoJSON spec: [longitude, latitude]
                        "coordinates": geom["coordinates"],
                    },
                }
            ],
        }

        out_path = os.path.join(OUTPUT_DIR, f"{route_id}.geojson")
        with open(out_path, "w") as f:
            json.dump(output, f, indent=2)

        coords = geom["coordinates"]
        n = len(coords)
        start = coords[0]
        end = coords[-1]
        print(f"  {route_id:25s} -> {out_path}  ({n} pts, {start} .. {end})")

    print("\nDone.")

if __name__ == "__main__":
    main()
