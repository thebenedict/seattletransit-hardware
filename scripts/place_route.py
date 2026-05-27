#!/usr/bin/env python3
"""Place a single line of route LEDs along an existing silkscreen path.

Run this with KiCad's bundled Python, not the system Python. The script reads
a named group of F.Silkscreen line segments, orders them into one open
polyline, and places WS2812B-2020 point LEDs along that path. WS2812B-MINI
ferryport LEDs are intentionally ignored.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import pcbnew
except ImportError as exc:
    raise SystemExit(
        "Could not import pcbnew. Run with KiCad's bundled Python, for example:\n"
        "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/"
        "Versions/3.9/bin/python3.9 scripts/place_route.py ..."
    ) from exc


Point = Tuple[float, float]
Vector = Tuple[float, float]
Segment = Tuple[Point, Point]
SilkCommand = Tuple[str, Point, Optional[Point], Point]

NM_PER_MM = 1_000_000
REF_RE = re.compile(r"^([^0-9]+)([0-9]+)$")

DEFAULTS: Dict[str, Any] = {
    "silk_layer": "F.Silkscreen",
    "silkscreen_group_prefix": "route:",
    "silk_fillet_radius_mm": 4.0,
    "silk_width_mm": 0.2,
    "generate_silk": True,
    "led_spacing_along_mm": 5.0,
    "start_clearance_mm": 0.0,
    "end_clearance_mm": 0.0,
    "led_rotation_offset_deg": 0.0,
    "connection_tolerance_mm": 0.01,
    "merge_collinear_segments": True,
    "strict_led_count": False,
    "hide_refs": True,
    "reverse_path": False,
    "reverse_refs": False,
}


@dataclass(frozen=True)
class LedPlacement:
    route_name: str
    index: int
    ref: str
    pos: Point
    angle_deg: float


@dataclass(frozen=True)
class RoutePath:
    points: List[Point]
    group_name: Optional[str]
    has_arcs: bool


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def mm_to_nm(value: float) -> int:
    return int(round(value * NM_PER_MM))


def nm_to_mm(value: int) -> float:
    return value / NM_PER_MM


def to_vec(point: Point) -> "pcbnew.VECTOR2I":
    return pcbnew.VECTOR2I(mm_to_nm(point[0]), mm_to_nm(point[1]))


def from_vec(vec: "pcbnew.VECTOR2I") -> Point:
    if hasattr(vec, "x") and hasattr(vec, "y"):
        return (nm_to_mm(vec.x), nm_to_mm(vec.y))
    return (nm_to_mm(vec[0]), nm_to_mm(vec[1]))


def add(a: Point, b: Vector) -> Point:
    return (a[0] + b[0], a[1] + b[1])


def sub(a: Point, b: Point) -> Vector:
    return (a[0] - b[0], a[1] - b[1])


def mul(a: Vector, scale: float) -> Vector:
    return (a[0] * scale, a[1] * scale)


def dot(a: Vector, b: Vector) -> float:
    return a[0] * b[0] + a[1] * b[1]


def cross(a: Vector, b: Vector) -> float:
    return a[0] * b[1] - a[1] * b[0]


def length(v: Vector) -> float:
    return math.hypot(v[0], v[1])


def unit(v: Vector) -> Vector:
    size = length(v)
    if size <= 1e-9:
        raise ValueError("zero-length vector")
    return (v[0] / size, v[1] / size)


def angle_degrees(v: Vector) -> float:
    # KiCad footprint rotation uses the opposite Y-axis sign from board coordinates.
    angle = math.degrees(math.atan2(-v[1], v[0]))
    if angle > 180:
        angle -= 360
    if angle <= -180:
        angle += 360
    return angle


def format_point(point: Point) -> str:
    return f"({point[0]:.3f}, {point[1]:.3f})"


def path_length(points: Sequence[Point]) -> float:
    return sum(length(sub(end, start)) for start, end in zip(points, points[1:]))


def point_along_polyline(points: Sequence[Point], distance_mm: float) -> Tuple[Point, Vector]:
    remaining = distance_mm
    for start, end in zip(points, points[1:]):
        seg = sub(end, start)
        seg_len = length(seg)
        if seg_len <= 1e-9:
            continue
        tangent = unit(seg)
        if remaining <= seg_len:
            return add(start, mul(tangent, remaining)), tangent
        remaining -= seg_len
    seg = sub(points[-1], points[-2])
    return points[-1], unit(seg)


def arc_midpoint(center: Point, start: Point, end: Point, sweep_sign: float) -> Point:
    radius = length(sub(start, center))
    start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
    end_angle = math.atan2(end[1] - center[1], end[0] - center[0])

    ccw_sweep = (end_angle - start_angle) % (2 * math.pi)
    cw_sweep = ccw_sweep - 2 * math.pi
    sweep = ccw_sweep if sweep_sign >= 0 else cw_sweep
    if sweep_sign >= 0 and sweep > math.pi:
        sweep = cw_sweep
    elif sweep_sign < 0 and abs(sweep) > math.pi:
        sweep = ccw_sweep

    mid_angle = start_angle + sweep / 2.0
    return (
        center[0] + radius * math.cos(mid_angle),
        center[1] + radius * math.sin(mid_angle),
    )


def fillet_polyline(points: Sequence[Point], radius_mm: float) -> List[SilkCommand]:
    if len(points) < 2:
        return []
    if radius_mm <= 1e-9 or len(points) < 3:
        return [("line", start, None, end) for start, end in zip(points, points[1:])]

    commands: List[SilkCommand] = []
    current = points[0]

    for index in range(1, len(points) - 1):
        prev_point = points[index - 1]
        vertex = points[index]
        next_point = points[index + 1]
        prev_len = length(sub(prev_point, vertex))
        next_len = length(sub(next_point, vertex))
        if prev_len <= 1e-9 or next_len <= 1e-9:
            continue

        into_prev = unit(sub(prev_point, vertex))
        into_next = unit(sub(next_point, vertex))
        cos_theta = max(-1.0, min(1.0, dot(into_prev, into_next)))
        theta = math.acos(cos_theta)
        if theta <= 1e-6 or abs(math.pi - theta) <= 1e-6:
            continue

        tangent_distance = radius_mm / math.tan(theta / 2.0)
        tangent_distance = min(tangent_distance, prev_len * 0.49, next_len * 0.49)
        if tangent_distance <= 1e-9:
            continue

        actual_radius = tangent_distance * math.tan(theta / 2.0)
        tangent_start = add(vertex, mul(into_prev, tangent_distance))
        tangent_end = add(vertex, mul(into_next, tangent_distance))
        bisector = add(into_prev, into_next)
        if length(bisector) <= 1e-9:
            continue

        center_distance = actual_radius / math.sin(theta / 2.0)
        center = add(vertex, mul(unit(bisector), center_distance))
        sweep_sign = -1.0 if cross(into_prev, into_next) > 0 else 1.0
        mid = arc_midpoint(center, tangent_start, tangent_end, sweep_sign)

        if length(sub(tangent_start, current)) > 1e-6:
            commands.append(("line", current, None, tangent_start))
        commands.append(("arc", tangent_start, mid, tangent_end))
        current = tangent_end

    if length(sub(points[-1], current)) > 1e-6:
        commands.append(("line", current, None, points[-1]))
    return commands


def arc_sweep_from_midpoint(center: Point, start: Point, mid: Point, end: Point) -> float:
    start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
    end_angle = math.atan2(end[1] - center[1], end[0] - center[0])
    mid_angle = math.atan2(mid[1] - center[1], mid[0] - center[0])

    ccw_sweep = (end_angle - start_angle) % (2 * math.pi)
    cw_sweep = ccw_sweep - 2 * math.pi

    def midpoint_error(sweep: float) -> float:
        angle = start_angle + sweep / 2.0
        return abs(math.atan2(math.sin(angle - mid_angle), math.cos(angle - mid_angle)))

    return ccw_sweep if midpoint_error(ccw_sweep) <= midpoint_error(cw_sweep) else cw_sweep


def sample_arc_points(shape: "pcbnew.PCB_SHAPE", max_step_mm: float = 1.0) -> List[Point]:
    start = from_vec(shape.GetStart())
    mid = from_vec(shape.GetArcMid())
    end = from_vec(shape.GetEnd())
    center = from_vec(shape.GetCenter())
    radius = length(sub(start, center))
    if radius <= 1e-9:
        return [start, end]

    sweep = arc_sweep_from_midpoint(center, start, mid, end)
    arc_len = abs(sweep) * radius
    steps = max(1, int(math.ceil(arc_len / max_step_mm)))
    start_angle = math.atan2(start[1] - center[1], start[0] - center[0])

    points: List[Point] = []
    for index in range(steps + 1):
        angle = start_angle + sweep * index / steps
        points.append((center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle)))
    return points


def get_layer_id(board: "pcbnew.BOARD", name: str) -> int:
    layer_id = board.GetLayerID(name)
    if layer_id != -1:
        return layer_id
    raise ValueError(f"layer {name!r} not found in board")


def get_group_name(item: Any) -> Optional[str]:
    if not hasattr(item, "GetParentGroup"):
        return None
    group = item.GetParentGroup()
    if not group:
        return None
    return group.GetName()


def merge_collinear_segments(segments: Sequence[Segment], tolerance_mm: float) -> List[Segment]:
    used = [False] * len(segments)
    merged: List[Segment] = []

    for index, base in enumerate(segments):
        if used[index]:
            continue
        used[index] = True
        origin = base[0]
        direction = unit(sub(base[1], base[0]))
        grouped = [base]

        for other_index in range(index + 1, len(segments)):
            other = segments[other_index]
            if used[other_index]:
                continue
            other_direction = unit(sub(other[1], other[0]))
            if abs(cross(direction, other_direction)) > 1e-6:
                continue
            if abs(cross(direction, sub(other[0], origin))) > tolerance_mm:
                continue
            if abs(cross(direction, sub(other[1], origin))) > tolerance_mm:
                continue
            used[other_index] = True
            grouped.append(other)

        intervals: List[Tuple[float, float]] = []
        for start_point, end_point in grouped:
            a = dot(sub(start_point, origin), direction)
            b = dot(sub(end_point, origin), direction)
            intervals.append((min(a, b), max(a, b)))

        intervals.sort()
        merged_intervals: List[Tuple[float, float]] = []
        for start, end in intervals:
            if not merged_intervals or start > merged_intervals[-1][1] + tolerance_mm:
                merged_intervals.append((start, end))
            else:
                prev_start, prev_end = merged_intervals[-1]
                merged_intervals[-1] = (prev_start, max(prev_end, end))

        for start, end in merged_intervals:
            merged.append((add(origin, mul(direction, start)), add(origin, mul(direction, end))))

    return merged


class PointClusterer:
    def __init__(self, tolerance_mm: float) -> None:
        self.tolerance_mm = tolerance_mm
        self.points_by_key: Dict[int, List[Point]] = {}
        self.centroids: Dict[int, Point] = {}
        self.next_key = 0

    def key_for(self, point: Point) -> int:
        for key, centroid in self.centroids.items():
            if length(sub(point, centroid)) <= self.tolerance_mm:
                self.points_by_key[key].append(point)
                pts = self.points_by_key[key]
                self.centroids[key] = (
                    sum(pt[0] for pt in pts) / len(pts),
                    sum(pt[1] for pt in pts) / len(pts),
                )
                return key

        key = self.next_key
        self.next_key += 1
        self.points_by_key[key] = [point]
        self.centroids[key] = point
        return key

    def canonical(self, key: int) -> Point:
        return self.centroids[key]


def order_segments(segments: Sequence[Segment], tolerance_mm: float) -> List[Point]:
    clusterer = PointClusterer(tolerance_mm)
    adjacency: Dict[int, List[Tuple[int, int]]] = {}

    for index, (start, end) in enumerate(segments):
        start_key = clusterer.key_for(start)
        end_key = clusterer.key_for(end)
        adjacency.setdefault(start_key, []).append((end_key, index))
        adjacency.setdefault(end_key, []).append((start_key, index))

    branches = [key for key, edges in adjacency.items() if len(edges) > 2]
    if branches:
        points = ", ".join(format_point(clusterer.canonical(key)) for key in branches)
        raise ValueError(f"silkscreen route branches are not supported: {points}")

    endpoints = [key for key, edges in adjacency.items() if len(edges) == 1]
    if len(endpoints) != 2:
        endpoint_text = ", ".join(format_point(clusterer.canonical(key)) for key in endpoints)
        raise ValueError(
            f"silkscreen route must be one open polyline; found {len(endpoints)} endpoint(s): "
            f"{endpoint_text}"
        )

    current = endpoints[0]
    used_segments = set()
    ordered = [clusterer.canonical(current)]

    while len(used_segments) < len(segments):
        choices = [
            (next_key, seg_index)
            for next_key, seg_index in adjacency[current]
            if seg_index not in used_segments
        ]
        if not choices:
            break
        if len(choices) > 1:
            raise ValueError("ambiguous silkscreen route traversal")
        next_key, seg_index = choices[0]
        used_segments.add(seg_index)
        ordered.append(clusterer.canonical(next_key))
        current = next_key

    if len(used_segments) != len(segments):
        raise ValueError("silkscreen route has disconnected line segments")
    return ordered


def find_silkscreen_segments(
    board: "pcbnew.BOARD",
    silk_layer: str,
    group_name: str,
    tolerance_mm: float,
    merge_collinear: bool,
) -> Tuple[List[Segment], bool]:
    layer_id = get_layer_id(board, silk_layer)
    segments: List[Segment] = []
    ignored_in_group: List[str] = []
    has_arcs = False

    for drawing in board.GetDrawings():
        if drawing.GetLayer() != layer_id:
            continue
        if get_group_name(drawing) != group_name:
            continue
        if type(drawing).__name__ != "PCB_SHAPE":
            ignored_in_group.append(type(drawing).__name__)
            continue

        start = from_vec(drawing.GetStart())
        end = from_vec(drawing.GetEnd())
        if drawing.GetShape() == pcbnew.SHAPE_T_SEGMENT and length(sub(end, start)) > 1e-6:
            segments.append((start, end))
        elif drawing.GetShape() == pcbnew.SHAPE_T_ARC and length(sub(end, start)) > 1e-6:
            arc_points = sample_arc_points(drawing)
            segments.extend(
                (arc_start, arc_end)
                for arc_start, arc_end in zip(arc_points, arc_points[1:])
                if length(sub(arc_end, arc_start)) > 1e-6
            )
            has_arcs = True
        else:
            ignored_in_group.append(drawing.GetShapeStr())

    if ignored_in_group:
        print(
            f"warning: ignored {len(ignored_in_group)} non-line silkscreen item(s) "
            f"in {group_name!r}: "
            + ", ".join(sorted(set(str(item) for item in ignored_in_group))),
            file=sys.stderr,
        )
    if not segments:
        raise ValueError(f"no line or arc segments found on layer {silk_layer!r} in group {group_name!r}")
    if merge_collinear and not has_arcs:
        return merge_collinear_segments(segments, tolerance_mm), has_arcs
    return segments, has_arcs


def parse_ref(ref: str) -> Tuple[str, int, int]:
    match = REF_RE.match(ref)
    if not match:
        raise ValueError(f"reference {ref!r} does not end in a numeric suffix")
    prefix, number, = match.groups()
    return prefix, int(number), len(number)


def expand_ref_token(token: str) -> List[str]:
    token = token.strip()
    if not token:
        raise ValueError("empty LED reference")

    separator = None
    if ".." in token:
        separator = ".."
    elif "-" in token:
        separator = "-"

    if separator is None:
        return [token]

    start_ref, end_ref = [part.strip() for part in token.split(separator, 1)]
    if not start_ref or not end_ref:
        raise ValueError(f"invalid reference range {token!r}")

    start_prefix, start_number, start_width = parse_ref(start_ref)
    end_prefix, end_number, end_width = parse_ref(end_ref)
    if start_prefix != end_prefix:
        raise ValueError(f"reference range {token!r} mixes prefixes")

    step = 1 if end_number >= start_number else -1
    width = max(start_width, end_width)
    return [f"{start_prefix}{number:0{width}d}" for number in range(start_number, end_number + step, step)]


def expand_refs(refs: Any) -> List[str]:
    if isinstance(refs, str):
        return expand_ref_token(refs)
    if not isinstance(refs, list):
        raise ValueError("'refs' must be a string or an array")

    expanded: List[str] = []
    for item in refs:
        if not isinstance(item, str):
            raise ValueError("'refs' entries must be strings")
        expanded.extend(expand_ref_token(item))
    return expanded


def natural_ref_key(ref: str) -> Tuple[str, int, str]:
    match = REF_RE.match(ref)
    if not match:
        return (ref, -1, ref)
    prefix, number = match.groups()
    return (prefix, int(number), ref)


def footprint_text(footprint: "pcbnew.FOOTPRINT") -> str:
    value = footprint.GetValue() if hasattr(footprint, "GetValue") else ""
    fpid = footprint.GetFPIDAsString() if hasattr(footprint, "GetFPIDAsString") else ""
    return f"{value} {fpid}".upper()


def is_point_led(footprint: "pcbnew.FOOTPRINT") -> bool:
    ref = footprint.GetReference()
    text = footprint_text(footprint)
    return ref.startswith("LED") and "WS2812B-2020" in text and "WS2812B-MINI" not in text


def is_mini_led(footprint: "pcbnew.FOOTPRINT") -> bool:
    text = footprint_text(footprint)
    return "WS2812B-MINI" in text or "L3.5-W3.5" in text


def schematic_name(value: str) -> str:
    value = os.path.basename(value.strip().strip("/"))
    if value.lower().endswith(".kicad_sch"):
        value = value[:-10]
    return value.lower()


def footprint_matches_schematic(footprint: "pcbnew.FOOTPRINT", schematic: str) -> bool:
    wanted = schematic_name(schematic)
    sheetfile = schematic_name(footprint.GetSheetfile()) if hasattr(footprint, "GetSheetfile") else ""
    sheetname = footprint.GetSheetname() if hasattr(footprint, "GetSheetname") else ""
    sheet_parts = [part for part in sheetname.split("/") if part]
    sheet_leaf = sheet_parts[-1].lower() if sheet_parts else ""
    return wanted in {sheetfile, sheet_leaf}


def collect_schematic_refs(board: "pcbnew.BOARD", schematic: str) -> List[str]:
    refs = [
        footprint.GetReference()
        for footprint in board.GetFootprints()
        if footprint_matches_schematic(footprint, schematic) and is_point_led(footprint)
    ]
    return sorted(refs, key=natural_ref_key)


def ref_source(route: Dict[str, Any]) -> Tuple[str, Any]:
    if "refs" in route:
        return "refs", route["refs"]
    for key in ("schematic", "schematic_name", "sheet", "sheet_name"):
        if key in route:
            return key, route[key]
    raise ValueError("route must specify either 'refs' or 'schematic'")


def route_refs(board: "pcbnew.BOARD", route_name: str, route: Dict[str, Any]) -> List[str]:
    source_key, source_value = ref_source(route)

    if source_key == "refs":
        requested_refs = expand_refs(source_value)
    else:
        requested_refs = collect_schematic_refs(board, str(source_value))
        if not requested_refs:
            raise ValueError(
                f"route {route_name!r} schematic {source_value!r} did not match any "
                "WS2812B-2020 point LED footprints"
            )

    refs: List[str] = []
    skipped: List[str] = []
    for ref in requested_refs:
        footprint = board.FindFootprintByReference(ref)
        if footprint is None:
            raise ValueError(f"route {route_name!r} references missing footprint {ref!r}")
        if is_mini_led(footprint) or not is_point_led(footprint):
            skipped.append(ref)
            continue
        refs.append(ref)

    if skipped:
        print(
            f"warning: route {route_name!r} skipped non-point LED(s): {', '.join(skipped)}",
            file=sys.stderr,
        )
    if route.get("reverse_refs", False):
        refs.reverse()
    if not refs:
        raise ValueError(f"route {route_name!r} has no WS2812B-2020 point LEDs to place")
    return refs


def route_path_from_config(
    board: "pcbnew.BOARD",
    route_name: str,
    route: Dict[str, Any],
) -> RoutePath:
    if "polyline_mm" in route:
        points = [tuple(map(float, point)) for point in route["polyline_mm"]]
        if len(points) < 2:
            raise ValueError("'polyline_mm' must contain at least two points")
        group_name = None
        has_arcs = False
    else:
        group_name = (
            route.get("silkscreen_group")
            or route.get("silk_group")
            or route.get("group")
            or f"{route.get('silkscreen_group_prefix', DEFAULTS['silkscreen_group_prefix'])}{route_name}"
        )
        tolerance_mm = float(route.get("connection_tolerance_mm", DEFAULTS["connection_tolerance_mm"]))
        segments, has_arcs = find_silkscreen_segments(
            board,
            str(route.get("silk_layer", DEFAULTS["silk_layer"])),
            str(group_name),
            tolerance_mm,
            bool(route.get("merge_collinear_segments", True)),
        )
        points = order_segments(segments, tolerance_mm)

    if route.get("reverse_path", False):
        points = list(reversed(points))
    return RoutePath(points, str(group_name) if group_name is not None else None, has_arcs)


def placement_distances(route_name: str, route: Dict[str, Any], count: int, total_length: float) -> List[float]:
    start_clearance = float(route.get("start_clearance_mm", 0.0))
    end_clearance = float(route.get("end_clearance_mm", 0.0))
    spacing = float(route.get("led_spacing_along_mm", DEFAULTS["led_spacing_along_mm"]))
    strict = bool(route.get("strict_led_count", DEFAULTS["strict_led_count"]))

    usable_length = total_length - start_clearance - end_clearance
    if usable_length <= 0:
        raise ValueError(
            f"route {route_name!r} has no usable path length after clearances "
            f"({total_length:.3f} mm path, {start_clearance:.3f} mm start, {end_clearance:.3f} mm end)"
        )
    if count == 1:
        return [start_clearance + usable_length / 2.0]

    requested_span = (count - 1) * spacing
    if requested_span > usable_length:
        if strict:
            raise ValueError(
                f"route {route_name!r} has {count} LED(s), requiring {requested_span:.3f} mm "
                f"at {spacing:.3f} mm spacing, but only {usable_length:.3f} mm fit"
            )
        print(
            f"warning: route {route_name!r} compressed spacing from {spacing:.3f} mm to "
            f"{usable_length / (count - 1):.3f} mm so all {count} LED(s) fit",
            file=sys.stderr,
        )
        requested_span = usable_length

    margin = (usable_length - requested_span) / 2.0
    step = requested_span / (count - 1)
    return [start_clearance + margin + index * step for index in range(count)]


def build_led_placements(route_name: str, route: Dict[str, Any], points: Sequence[Point], refs: Sequence[str]) -> List[LedPlacement]:
    total_length = path_length(points)
    distances = placement_distances(route_name, route, len(refs), total_length)
    rotation_offset = float(route.get("led_rotation_offset_deg", DEFAULTS["led_rotation_offset_deg"]))

    placements: List[LedPlacement] = []
    for index, (ref, distance_mm) in enumerate(zip(refs, distances)):
        point, tangent = point_along_polyline(points, distance_mm)
        angle = angle_degrees(tangent) + rotation_offset
        placements.append(LedPlacement(route_name, index, ref, point, angle))
    return placements


def set_footprint_pose(board: "pcbnew.BOARD", ref: str, pos: Point, angle_deg: float) -> None:
    footprint = board.FindFootprintByReference(ref)
    if footprint is None:
        raise ValueError(f"footprint {ref!r} not found")
    footprint.SetPosition(to_vec(pos))
    footprint.SetOrientationDegrees(angle_deg)


def hide_footprint_reference(board: "pcbnew.BOARD", ref: str) -> bool:
    footprint = board.FindFootprintByReference(ref)
    if footprint is None:
        raise ValueError(f"footprint {ref!r} not found")
    field = footprint.Reference()
    was_visible = field.IsVisible()
    was_force_visible = bool(field.IsForceVisible()) if hasattr(field, "IsForceVisible") else False
    field.SetVisible(False)
    if hasattr(field, "SetForceVisible"):
        field.SetForceVisible(False)
    return was_visible or was_force_visible


def delete_group(board: "pcbnew.BOARD", group_name: str) -> None:
    for group in list(board.Groups()):
        if group.GetName() != group_name:
            continue
        for item in list(group.GetItems()):
            group.RemoveItem(item)
            board.Delete(item)
        board.Delete(group)


def add_silk_segment(
    board: "pcbnew.BOARD",
    group: "pcbnew.PCB_GROUP",
    layer_id: int,
    start: Point,
    end: Point,
    width_mm: float,
) -> None:
    shape = pcbnew.PCB_SHAPE(board)
    shape.SetShape(pcbnew.SHAPE_T_SEGMENT)
    shape.SetLayer(layer_id)
    shape.SetStart(to_vec(start))
    shape.SetEnd(to_vec(end))
    shape.SetWidth(mm_to_nm(width_mm))
    board.Add(shape)
    group.AddItem(shape)


def add_silk_arc(
    board: "pcbnew.BOARD",
    group: "pcbnew.PCB_GROUP",
    layer_id: int,
    start: Point,
    mid: Point,
    end: Point,
    width_mm: float,
) -> None:
    shape = pcbnew.PCB_SHAPE(board)
    shape.SetShape(pcbnew.SHAPE_T_ARC)
    shape.SetLayer(layer_id)
    shape.SetArcGeometry(to_vec(start), to_vec(mid), to_vec(end))
    shape.SetWidth(mm_to_nm(width_mm))
    board.Add(shape)
    group.AddItem(shape)


def regenerate_filleted_silk(
    board: "pcbnew.BOARD",
    route: Dict[str, Any],
    group_name: str,
    points: Sequence[Point],
) -> int:
    layer_id = get_layer_id(board, str(route.get("silk_layer", DEFAULTS["silk_layer"])))
    width_mm = float(route.get("silk_width_mm", DEFAULTS["silk_width_mm"]))
    radius_mm = float(route.get("silk_fillet_radius_mm", DEFAULTS["silk_fillet_radius_mm"]))
    commands = fillet_polyline(points, radius_mm)

    delete_group(board, group_name)
    group = pcbnew.PCB_GROUP(board)
    group.SetName(group_name)
    board.Add(group)

    for command, start, mid, end in commands:
        if command == "line":
            add_silk_segment(board, group, layer_id, start, end, width_mm)
        elif command == "arc" and mid is not None:
            add_silk_arc(board, group, layer_id, start, mid, end, width_mm)
        else:
            raise ValueError(f"unknown silk command {command!r}")
    return len(commands)


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    if "routes" not in config or not isinstance(config["routes"], dict):
        raise ValueError("config must contain a 'routes' object")
    return config


def selected_routes(config: Dict[str, Any], names: Sequence[str]) -> Iterable[Tuple[str, Dict[str, Any]]]:
    defaults = deep_merge(DEFAULTS, config.get("defaults", {}))
    routes = config["routes"]
    if names:
        route_names = list(names)
    else:
        route_names = list(routes.keys())

    for route_name in route_names:
        route_default = {
            "silkscreen_group": f"{defaults.get('silkscreen_group_prefix', DEFAULTS['silkscreen_group_prefix'])}{route_name}",
            "schematic": route_name,
        }
        if route_name not in routes:
            print(
                f"warning: route {route_name!r} not found in config; using "
                f"silkscreen_group={route_default['silkscreen_group']!r}, "
                f"schematic={route_default['schematic']!r}",
                file=sys.stderr,
            )
        route = deep_merge(route_default, dict(routes.get(route_name, {})))
        yield route_name, deep_merge(defaults, route)


def check_lock_file(board_path: str, output_path: Optional[str], force: bool) -> None:
    if output_path and os.path.abspath(output_path) != os.path.abspath(board_path):
        return
    lock_path = os.path.join(os.path.dirname(board_path), f"~{os.path.basename(board_path)}.lck")
    if os.path.exists(lock_path) and not force:
        raise ValueError(
            f"KiCad lock file exists: {lock_path}. Close the board, use --output for a copy, "
            "or pass --force if you intentionally want to write anyway."
        )


def report_route(route_name: str, source: Tuple[str, Any], points: Sequence[Point], refs: Sequence[str], placements: Sequence[LedPlacement]) -> None:
    print(
        f"route {route_name!r}: {len(refs)} LED(s) from {source[0]}={source[1]!r}, "
        f"{len(points) - 1} silkscreen segment(s), {path_length(points):.3f} mm path"
    )
    for placement in placements:
        print(
            f"  {placement.ref}: {format_point(placement.pos)} "
            f"{placement.angle_deg:.1f} deg"
        )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", default="transit.kicad_pcb", help="KiCad board file to read")
    parser.add_argument("--config", required=True, help="route JSON config file")
    parser.add_argument("--route", action="append", default=[], help="only process this route; repeatable")
    parser.add_argument(
        "--reverse-refs",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="override reverse_refs for selected routes",
    )
    parser.add_argument(
        "--reverse-path",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="override reverse_path for selected routes",
    )
    parser.add_argument("--apply", action="store_true", help="write footprint changes")
    parser.add_argument("--output", help="write to this board path instead of overwriting --board")
    parser.add_argument("--force", action="store_true", help="allow in-place writes while a KiCad lock file exists")
    return parser.parse_args(argv)


def apply_cli_overrides(route: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    route = dict(route)
    if args.reverse_refs is not None:
        route["reverse_refs"] = args.reverse_refs
    if args.reverse_path is not None:
        route["reverse_path"] = args.reverse_path
    return route


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    board = pcbnew.LoadBoard(args.board)
    config = load_config(args.config)

    processed = 0
    for route_name, route in selected_routes(config, args.route):
        route = apply_cli_overrides(route, args)
        path = route_path_from_config(board, route_name, route)
        points = path.points
        source = ref_source(route)
        refs = route_refs(board, route_name, route)
        placements = build_led_placements(route_name, route, points, refs)
        report_route(route_name, source, points, refs, placements)

        if args.apply:
            for placement in placements:
                set_footprint_pose(board, placement.ref, placement.pos, placement.angle_deg)
            if bool(route.get("hide_refs", DEFAULTS["hide_refs"])):
                hidden_count = sum(1 for placement in placements if hide_footprint_reference(board, placement.ref))
                if hidden_count:
                    print(f"  hid {hidden_count} reference designator(s)")
            if bool(route.get("generate_silk", DEFAULTS["generate_silk"])):
                if path.group_name is None:
                    print("  warning: no silkscreen group to rewrite from polyline_mm", file=sys.stderr)
                elif path.has_arcs and not bool(route.get("refillet_existing_silk", False)):
                    print("  left existing filleted silkscreen unchanged")
                else:
                    generated_count = regenerate_filleted_silk(board, route, path.group_name, points)
                    print(f"  rewrote silkscreen group {path.group_name!r} with {generated_count} item(s)")
        processed += 1

    if args.apply:
        output_path = args.output or args.board
        check_lock_file(args.board, output_path, args.force)
        pcbnew.SaveBoard(output_path, board)
        print(f"wrote {output_path}")
    else:
        print("dry run only; pass --apply to write changes")

    print(f"processed {processed} route(s)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
