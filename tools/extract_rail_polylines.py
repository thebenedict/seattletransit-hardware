#!/usr/bin/env python3
"""
Extract rail and monorail route polylines from GTFS data and write
one GeoJSON file per route to gis-data/polylines/.

One polyline per route is sufficient: both directions share the same physical
track, so the reversed direction's ledProgress values are just the mirror image.
We use direction_id=0 (southbound / eastbound) as the canonical shape.

Strategy: pick the shape_id with the most points for the chosen direction —
that reliably selects the longest / full-extent polyline over short-turn shapes.
"""

import csv
import json
import os
from collections import Counter

OUTPUT_DIR = "gis-data/polylines"

# (gtfs_dir, route_id_in_gtfs, direction_id, output_filename)
TARGETS = [
    ("gis-data/sound_transit_gtfs", "100479", "0", "link-1"),
    ("gis-data/sound_transit_gtfs", "2LINE",  "0", "link-2"),
    ("gis-data/monorail-gtfs",      "SCM",    "0", "monorail"),
]


def pick_dominant_shape(gtfs_dir, route_id, direction_id):
    """Return the shape_id with the most points for this route+direction.

    We want the full-extent shape (covering the entire line), not the most
    frequently run shape — those are often short-turn trips on a partial segment.
    Using most points reliably selects the longest / most complete polyline.
    """
    trip_shapes = set()
    with open(os.path.join(gtfs_dir, "trips.txt")) as f:
        for row in csv.DictReader(f):
            if row["route_id"] == route_id and row["direction_id"] == direction_id:
                trip_shapes.add(row["shape_id"])

    if not trip_shapes:
        return None, 0

    # Count shape points per shape_id
    point_counts = Counter()
    with open(os.path.join(gtfs_dir, "shapes.txt")) as f:
        for row in csv.DictReader(f):
            if row["shape_id"] in trip_shapes:
                point_counts[row["shape_id"]] += 1

    shape_id, n_pts = point_counts.most_common(1)[0]

    # Also report trip count for logging
    trip_count = sum(
        1 for s in trip_shapes if s == shape_id
    )
    with open(os.path.join(gtfs_dir, "trips.txt")) as f:
        trip_count = sum(
            1 for row in csv.DictReader(f)
            if row["route_id"] == route_id
            and row["direction_id"] == direction_id
            and row["shape_id"] == shape_id
        )

    return shape_id, trip_count


def load_shape(gtfs_dir, shape_id):
    """Return ordered list of [lat, lng] for the given shape_id."""
    pts = []
    with open(os.path.join(gtfs_dir, "shapes.txt")) as f:
        for row in csv.DictReader(f):
            if row["shape_id"] == shape_id:
                pts.append((
                    int(row["shape_pt_sequence"]),
                    float(row["shape_pt_lat"]),
                    float(row["shape_pt_lon"]),
                ))
    pts.sort()
    # GeoJSON spec: [longitude, latitude]
    return [[lng, lat] for _, lat, lng in pts]


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for gtfs_dir, route_id, direction_id, out_name in TARGETS:
        shape_id, trip_count = pick_dominant_shape(gtfs_dir, route_id, direction_id)
        if shape_id is None:
            print(f"WARNING: no trips found for route={route_id} dir={direction_id}")
            continue

        coords = load_shape(gtfs_dir, shape_id)

        output = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "route_id": out_name,
                        "gtfs_route_id": route_id,
                        "gtfs_shape_id": shape_id,
                        "gtfs_direction_id": int(direction_id),
                        "source": gtfs_dir,
                        "trip_count": trip_count,
                    },
                    "geometry": {
                        "type": "LineString",
                        "coordinates": coords,
                    },
                }
            ],
        }

        out_path = os.path.join(OUTPUT_DIR, f"{out_name}.geojson")
        with open(out_path, "w") as f:
            json.dump(output, f, indent=2)

        start, end = coords[0], coords[-1]
        print(
            f"  {out_name:25s} shape={shape_id:15s} "
            f"({len(coords)} pts, {start} .. {end})"
        )

    print("\nDone.")


if __name__ == "__main__":
    main()
