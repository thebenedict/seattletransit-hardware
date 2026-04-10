#!/usr/bin/env python3
"""
generate_board_svg.py

Reads transit-board.svg and transit.kicad_pcb, then produces
web/public/board-rev-a.svg with:
  - Map artwork intact (layer3 Traces layer, non-footprint layer1 groups)
  - 247 <circle> elements replacing LED footprint silkscreen groups
  - Non-LED component silkscreen groups deleted

Each circle carries:
    cx / cy         — PCB mm coordinate, within the existing transform context
    data-led-index  — global LED index (0-246) from rev-a.json
    data-led-ref    — schematic ref (e.g. LED201)
    class           — "led-marker"
    r               — 1.0 (2020 package) or 1.5 (mini package)
"""

import json
import math
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

PCB_FILE  = "../transit.kicad_pcb"
SVG_IN    = "transit-board.svg"
JSON_FILE = "led-maps/rev-a.json"
SVG_OUT   = "../web/public/board-rev-a.svg"

MATCH_TOLERANCE_MM = 2.5   # max distance from group centroid to footprint centre
SVG_NS = "http://www.w3.org/2000/svg"

# ---------------------------------------------------------------------------
# Preserve all namespace declarations from the source SVG
# ---------------------------------------------------------------------------
NAMESPACES = {
    "":          SVG_NS,
    "xlink":     "http://www.w3.org/1999/xlink",
    "dc":        "http://purl.org/dc/elements/1.1/",
    "cc":        "http://creativecommons.org/ns#",
    "rdf":       "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "sodipodi":  "http://sodipodi.sourceforge.net/DTD/sodipodi-0.0.dtd",
    "inkscape":  "http://www.inkscape.org/namespaces/inkscape",
}
for prefix, uri in NAMESPACES.items():
    ET.register_namespace(prefix, uri)


# ---------------------------------------------------------------------------
# SVG path parser — returns absolute bounding-box centre
# ---------------------------------------------------------------------------

def bbox_center(d: str):
    """Parse a path d-attribute and return its (x_mid, y_mid) in absolute coords."""
    xs, ys = [], []
    cx, cy = 0.0, 0.0
    tokens = re.findall(r"[MmLlHhVvZzCcSsQqTtAa]|[-+]?(?:\d+\.?\d*|\.\d+)", d)
    i, cmd = 0, "M"
    while i < len(tokens):
        t = tokens[i]
        if t.isalpha():
            cmd = t
            i += 1
            continue
        if cmd in ("M", "L"):
            cx, cy = float(tokens[i]), float(tokens[i + 1])
            xs.append(cx); ys.append(cy)
            i += 2
            cmd = "L" if cmd == "M" else cmd
        elif cmd in ("m", "l"):
            cx += float(tokens[i]); cy += float(tokens[i + 1])
            xs.append(cx); ys.append(cy)
            i += 2
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
            xs.append(cx); ys.append(cy)
            i += 7
        elif cmd in ("C", "c"):
            i += 6
        elif cmd in ("S", "s", "Q", "q"):
            i += 4
        elif cmd in ("T", "t"):
            i += 2
        else:
            i += 1
    if not xs:
        return None
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


def group_bbox_center(g):
    """Return bounding-box centre of all paths in a group (recursive)."""
    all_xs, all_ys = [], []
    for child in g.iter():
        d = child.get("d")
        if d:
            c = bbox_center(d)
            if c:
                all_xs.append(c[0]); all_ys.append(c[1])
    if all_xs:
        return (min(all_xs) + max(all_xs)) / 2, (min(all_ys) + max(all_ys)) / 2
    return None


def is_component_silkscreen(g) -> bool:
    """
    Distinguish LED / component silkscreen groups from map artwork.

    Component silkscreen uses stroke-width 0.15 (2020 LEDs) or 0.2 (MINI LEDs
    and some non-LED pads).  Art groups often have wider strokes and/or contain
    arcs with radius > 1 mm.
    """
    style = g.get("style", "")
    sw_m = re.search(r"stroke-width:([\d.]+)", style)
    stroke_w = float(sw_m.group(1)) if sw_m else 0.0
    if stroke_w > 0.21:
        return False
    for child in g:
        d = child.get("d", "")
        for arc_m in re.finditer(r"[Aa]\s*([\d.]+)\s*,\s*([\d.]+)", d):
            if float(arc_m.group(1)) > 1.0:
                return False
    return True


# ---------------------------------------------------------------------------
# Parse kicad_pcb — extract every footprint position
# ---------------------------------------------------------------------------

def parse_pcb_footprints(pcb_path: str) -> dict:
    with open(pcb_path) as f:
        content = f.read()
    footprints = {}
    pattern = re.compile(
        r"\(footprint\s+\"[^\"]*\"[^(]*"
        r"\(layer\s+\"[^\"]*\"\)[^(]*"
        r"\(uuid\s+\"[^\"]*\"\)[^(]*"
        r"\(at\s+([\d.+-]+)\s+([\d.+-]+)(?:\s+[\d.+-]+)?\)"
    )
    for m in pattern.finditer(content):
        x, y = float(m.group(1)), float(m.group(2))
        chunk = content[m.start(): m.start() + 800]
        ref_m = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', chunk)
        if ref_m:
            footprints[ref_m.group(1)] = (x, y)
    return footprints


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # --- load LED map --------------------------------------------------
    with open(JSON_FILE) as f:
        led_map = json.load(f)

    led_by_ref   = {e["ref"]: e["index"]   for e in led_map["leds"]}
    led_package  = {e["ref"]: e["package"] for e in led_map["leds"]}

    # radius by package
    def led_radius(ref):
        return 1.5 if led_package.get(ref) == "mini" else 1.0

    # --- parse PCB footprints ------------------------------------------
    footprints = parse_pcb_footprints(PCB_FILE)
    fp_list = list(footprints.items())
    print(f"PCB footprints: {len(footprints)}  "
          f"({len(led_by_ref)} LEDs in rev-a.json)")

    # --- parse SVG -----------------------------------------------------
    tree = ET.parse(SVG_IN)
    root = tree.getroot()

    def find_by_id(elem_id):
        for e in root.iter():
            if e.get("id") == elem_id:
                return e
        return None

    layer1 = find_by_id("layer1")
    if layer1 is None:
        sys.exit("Could not find layer1 (Silkscreen) in SVG")

    # Strip layer2 (Front copper) — not needed for web display
    layer2 = find_by_id("layer2")
    if layer2 is not None:
        root.remove(layer2)
        print("Removed layer2 (Front copper).")

    # Strip Inkscape editor metadata
    for tag in ("namedview", "metadata"):
        for ns in ("http://sodipodi.sourceforge.net/DTD/sodipodi-0.0.dtd",
                   "http://www.w3.org/2000/svg"):
            elem = root.find(f"{{{ns}}}{tag}")
            if elem is not None:
                root.remove(elem)

    # --- match each layer1 group to its nearest footprint -------------
    # group_id -> (ref, dist, centroid)
    group_to_fp: dict[str, tuple] = {}

    for g in list(layer1):
        if not g.tag.endswith("}g"):
            continue
        c = group_bbox_center(g)
        if c is None:
            continue
        best_dist = float("inf")
        best_ref  = None
        for ref, (fx, fy) in fp_list:
            d = math.hypot(c[0] - fx, c[1] - fy)
            if d < best_dist:
                best_dist, best_ref = d, ref

        if best_dist <= MATCH_TOLERANCE_MM and is_component_silkscreen(g):
            group_to_fp[g.get("id")] = (best_ref, best_dist, c)

    # --- verify coverage -----------------------------------------------
    matched_led_refs = {info[0] for info in group_to_fp.values()
                        if info[0] in led_by_ref}
    missing = set(led_by_ref.keys()) - matched_led_refs
    if missing:
        print(f"WARNING: {len(missing)} LED(s) not matched in SVG: {sorted(missing)}")
    else:
        print(f"All 247 LEDs matched in layer1.")

    # --- remove matched groups from layer1 ----------------------------
    # For LED footprints: collect the PCB position for circle placement.
    # All matched groups (LED and non-LED) are removed from the layer.
    led_circles: dict[str, dict] = {}   # ref -> {cx, cy, r, index}

    groups_to_remove = []
    for g in list(layer1):
        gid = g.get("id", "")
        if gid not in group_to_fp:
            continue
        ref, dist, centroid = group_to_fp[gid]
        groups_to_remove.append(g)

        if ref in led_by_ref and ref not in led_circles:
            # Use the PCB position (more accurate than centroid) for cx/cy
            pcb_x, pcb_y = footprints[ref]
            led_circles[ref] = {
                "cx":    pcb_x,
                "cy":    pcb_y,
                "r":     led_radius(ref),
                "index": led_by_ref[ref],
                "ref":   ref,
            }

    for g in groups_to_remove:
        layer1.remove(g)

    # Strip ALL remaining layer1 art groups (silkscreen markings, board
    # outline, text) — only the led-markers group we're about to add is needed.
    for g in list(layer1):
        if g.get("id") != "led-markers":
            layer1.remove(g)

    n_led_groups    = sum(1 for g in groups_to_remove
                          if group_to_fp[g.get("id","")][0] in led_by_ref)
    n_other_groups  = len(groups_to_remove) - n_led_groups
    print(f"Removed {n_led_groups} LED silkscreen groups, "
          f"{n_other_groups} non-LED component groups.")

    # --- insert LED circles -------------------------------------------
    # Place them in a new <g> inside layer1 that carries the same
    # per-group transform as the original silkscreen groups so the
    # PCB coordinates land in the right place on canvas.
    #
    # All layer1 child groups share translate(460.65081,-6.7299563).
    # Combined with layer1's own translate(-471.26215,79.266311):
    #   canvas = PCB + (460.65081-471.26215, -6.7299563+79.266311)
    #          = PCB + (-10.61134, +72.5364)
    GROUP_TRANSFORM = "translate(460.65081,-6.7299563)"

    marker_group = ET.SubElement(layer1, f"{{{SVG_NS}}}g")
    marker_group.set("id", "led-markers")
    marker_group.set("class", "led-markers")
    marker_group.set("transform", GROUP_TRANSFORM)

    for ref, info in sorted(led_circles.items(), key=lambda kv: kv[1]["index"]):
        circle = ET.SubElement(marker_group, f"{{{SVG_NS}}}circle")
        circle.set("cx",             f"{info['cx']:.4f}")
        circle.set("cy",             f"{info['cy']:.4f}")
        circle.set("r",              f"{info['r']}")
        circle.set("data-led-index", str(info["index"]))
        circle.set("data-led-ref",   ref)
        circle.set("class",          "led-marker")

    print(f"Inserted {len(led_circles)} LED circles.")

    # --- write output --------------------------------------------------
    os.makedirs(os.path.dirname(SVG_OUT), exist_ok=True)

    # Inject a <style> block for default (off-state) styling
    # --- convert inline styles → CSS classes (huge size reduction) -----------
    # layer3 (Traces) has 3000+ elements with only ~13 unique inline styles.
    # Replacing style="..." with class="sN" shrinks ~300 KB to ~6 KB.
    layer3 = find_by_id("layer3")
    map_css = ""
    if layer3 is not None:
        unique_styles: dict[str, str] = {}
        for elem in layer3.iter():
            style = elem.get("style")
            if style and style not in unique_styles:
                unique_styles[style] = f"s{len(unique_styles)}"

        map_css = "\n".join(
            f".{cls}{{{style}}}"
            for style, cls in unique_styles.items()
        )

        for elem in layer3.iter():
            style = elem.get("style")
            if style and style in unique_styles:
                elem.set("class", unique_styles[style])
                del elem.attrib["style"]
            if "id" in elem.attrib and elem is not layer3:
                del elem.attrib["id"]

        print(f"Converted {len(unique_styles)} unique styles to CSS classes in layer3.")

    # --- inject <style> block ---------------------------------------------------
    defs = root.find(f"{{{SVG_NS}}}defs")
    if defs is None:
        defs = ET.SubElement(root, f"{{{SVG_NS}}}defs")
        root.insert(0, defs)

    style_elem = ET.SubElement(defs, f"{{{SVG_NS}}}style")
    style_elem.set("type", "text/css")
    style_elem.text = (
        ".led-marker{fill:#1a1a2e;stroke:none;opacity:.4}"
        ".led-marker.active{fill:inherit;opacity:1}"
        + ("\n" + map_css if map_css else "")
    )

    tree.write(SVG_OUT, encoding="unicode", xml_declaration=True)
    print(f"Wrote {SVG_OUT}")

    # --- validate ------------------------------------------------------
    with open(SVG_OUT) as f:
        out_content = f.read()
    circle_count = out_content.count('class="led-marker"')
    print(f"Validation: {circle_count} led-marker circles in output "
          f"({'✓' if circle_count == 247 else '✗ expected 247'})")


if __name__ == "__main__":
    main()
