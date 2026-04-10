#!/usr/bin/env python3
"""
prune_svg_islands.py

Remove tiny subpaths (small dots and islands) from the filled background
path in board-rev-a-simplified.svg.

The layer2 'Front copper' path encodes all landmass shapes as subpaths
within a single <path d="..."> element.  The vast majority are tiny
noise artefacts with bounding-box area < 10 SVG units².  Only a handful
are real geographic features worth keeping.

Usage:
    python3 prune_svg_islands.py [--threshold 50] [--dry-run]

Outputs board-rev-a-clean.svg next to the input file.
"""

import argparse
import re
import sys
import xml.etree.ElementTree as ET

NAMESPACES = {
    "":          "http://www.w3.org/2000/svg",
    "xlink":     "http://www.w3.org/1999/xlink",
    "dc":        "http://purl.org/dc/elements/1.1/",
    "cc":        "http://creativecommons.org/ns#",
    "rdf":       "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "sodipodi":  "http://sodipodi.sourceforge.net/DTD/sodipodi-0.0.dtd",
    "inkscape":  "http://www.inkscape.org/namespaces/inkscape",
}
for prefix, uri in NAMESPACES.items():
    ET.register_namespace(prefix, uri)

SVG_IN  = "../web/public/board-rev-a-simplified.svg"
SVG_OUT = "../web/public/board-rev-a-clean.svg"


# ---------------------------------------------------------------------------
# Subpath bbox
# ---------------------------------------------------------------------------

def subpath_bbox(d_chunk: str):
    """Return (xmin, ymin, xmax, ymax) for a single M-started subpath."""
    xs, ys = [], []
    cx, cy = 0.0, 0.0
    tokens = re.findall(r"[MmLlHhVvZzCcSsQqTtAa]|[-+]?(?:\d+\.?\d*|\.\d+)", d_chunk)
    i, cmd = 0, "M"
    while i < len(tokens):
        t = tokens[i]
        if t.isalpha():
            cmd = t; i += 1; continue
        try:
            if cmd in ("M", "L"):
                cx, cy = float(tokens[i]), float(tokens[i + 1])
                xs.append(cx); ys.append(cy); i += 2
                cmd = "L" if cmd == "M" else cmd
            elif cmd in ("m", "l"):
                cx += float(tokens[i]); cy += float(tokens[i + 1])
                xs.append(cx); ys.append(cy); i += 2
                cmd = "l" if cmd == "m" else cmd
            elif cmd == "H":
                cx = float(tokens[i]); xs.append(cx); ys.append(cy); i += 1
            elif cmd == "h":
                cx += float(tokens[i]); xs.append(cx); ys.append(cy); i += 1
            elif cmd == "V":
                cy = float(tokens[i]); xs.append(cx); ys.append(cy); i += 1
            elif cmd == "v":
                cy += float(tokens[i]); xs.append(cx); ys.append(cy); i += 1
            elif cmd in ("A", "a"):
                if len(tokens) < i + 7:
                    break
                if cmd == "A":
                    cx, cy = float(tokens[i + 5]), float(tokens[i + 6])
                else:
                    cx += float(tokens[i + 5]); cy += float(tokens[i + 6])
                xs.append(cx); ys.append(cy); i += 7
            elif cmd in ("C", "c"):
                i += 6
            elif cmd in ("S", "s", "Q", "q"):
                i += 4
            elif cmd in ("T", "t"):
                i += 2
            elif cmd in ("Z", "z"):
                pass
            else:
                i += 1
        except (IndexError, ValueError):
            i += 1
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def bbox_area(bb) -> float:
    return (bb[2] - bb[0]) * (bb[3] - bb[1]) if bb else 0.0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--threshold", type=float, default=50.0,
                        help="Remove subpaths whose bbox area < threshold (default: 50)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print statistics without writing output")
    args = parser.parse_args()

    tree = ET.parse(SVG_IN)
    root = tree.getroot()

    # Find the layer2 path
    layer2 = None
    for e in root.iter():
        if e.get("id") == "layer2":
            layer2 = e
            break
    if layer2 is None:
        sys.exit("layer2 not found in SVG")

    path_elem = list(layer2)[0]
    d = path_elem.get("d", "")

    # Split on subpath boundaries (each starting M or m)
    raw = re.split(r"(?=[Mm]\s)", d)
    subpaths = [s for s in raw if s.strip()]

    areas = [(subpath_bbox(sp), sp) for sp in subpaths]

    kept, removed = [], []
    for bb, sp in areas:
        a = bbox_area(bb)
        if a >= args.threshold:
            kept.append((a, sp))
        else:
            removed.append((a, sp))

    print(f"Subpaths total:   {len(subpaths)}")
    print(f"Threshold:        {args.threshold}")
    print(f"Kept:             {len(kept)}")
    print(f"Removed:          {len(removed)}")

    if removed:
        removed_sorted = sorted(removed, key=lambda x: -x[0])
        print(f"\nLargest removed (top 10):")
        for a, sp in removed_sorted[:10]:
            bb = subpath_bbox(sp)
            cx = (bb[0] + bb[2]) / 2 if bb else 0
            cy = (bb[1] + bb[3]) / 2 if bb else 0
            print(f"  area={a:7.1f}  center=({cx:.0f},{cy:.0f})")

    if args.dry_run:
        return

    new_d = " ".join(sp for _, sp in kept)
    path_elem.set("d", new_d)

    tree.write(SVG_OUT, encoding="unicode", xml_declaration=True)
    print(f"\nWrote {SVG_OUT}")

    # Size report
    import os
    in_kb  = os.path.getsize(SVG_IN)  // 1024
    out_kb = os.path.getsize(SVG_OUT) // 1024
    print(f"Size: {in_kb} KB → {out_kb} KB")


if __name__ == "__main__":
    main()
