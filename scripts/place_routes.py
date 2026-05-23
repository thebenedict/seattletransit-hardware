#!/usr/bin/env python3
"""Place directional route LEDs and generated silkscreen from KiCad construction lines.

Run this with KiCad's bundled Python, not the system Python. The script reads
named graphic-line groups from a construction layer, computes directional LED
positions on each straight segment, and optionally regenerates the route's
parallel silkscreen lines.
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
        "Versions/3.9/bin/python3.9 scripts/place_routes.py ..."
    ) from exc


Point = Tuple[float, float]
Vector = Tuple[float, float]
Segment = Tuple[Point, Point]
SilkCommand = Tuple[str, Point, Optional[Point], Point]

NM_PER_MM = 1_000_000
REF_RE = re.compile(r"^([^0-9]+)([0-9]+)$")
DEFAULTS: Dict[str, Any] = {
    "construction_layer": "Route construction",
    "silk_layer": "F.Silkscreen",
    "led_spacing_along_mm": 5.0,
    "line_spacing_across_mm": 4.0,
    "silk_parallel_gap_mm": 4.0,
    "silk_fillet_radius_mm": 4.0,
    "silk_width_mm": 0.2,
    "endpoint_clearance_mm": 0.0,
    "endpoint_orientation": "along_route",
    "endpoint_start_rotation_offset_deg": -90.0,
    "endpoint_end_rotation_offset_deg": 0.0,
    "first_direction_side": "left",
    "mirror_led_rotations_for_power": True,
    "led_rotation_offset_deg": 0.0,
    "connection_tolerance_mm": 0.01,
    "strict_led_count": True,
    "generate_silk": True,
}


@dataclass
class LedPlacement:
    route_name: str
    direction: str
    index: int
    segment_index: int
    ref: str
    pos: Point
    angle_deg: float


@dataclass
class EndpointPlacement:
    route_name: str
    name: str
    ref: str
    pos: Point
    angle_deg: float


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
    return (nm_to_mm(vec.x), nm_to_mm(vec.y))


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


def left_normal(v: Vector) -> Vector:
    return (-v[1], v[0])


def angle_degrees(v: Vector) -> float:
    angle = math.degrees(math.atan2(v[1], v[0]))
    if angle > 180:
        angle -= 360
    if angle <= -180:
        angle += 360
    return angle


def line_intersection(p: Point, r: Vector, q: Point, s: Vector) -> Optional[Point]:
    denom = cross(r, s)
    if abs(denom) < 1e-9:
        return None
    t = cross(sub(q, p), s) / denom
    return add(p, mul(r, t))


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
    tangent = unit(seg)
    return points[-1], tangent


def path_length(points: Sequence[Point]) -> float:
    return sum(length(sub(end, start)) for start, end in zip(points, points[1:]))


def trim_polyline(points: Sequence[Point], trim_start_mm: float, trim_end_mm: float) -> List[Point]:
    if len(points) < 2:
        return list(points)

    total = sum(length(sub(end, start)) for start, end in zip(points, points[1:]))
    if trim_start_mm + trim_end_mm >= total:
        raise ValueError(
            f"silk trim ({trim_start_mm}+{trim_end_mm} mm) is longer than route ({total:.3f} mm)"
        )

    start_point, _ = point_along_polyline(points, trim_start_mm)
    end_point, _ = point_along_polyline(points, total - trim_end_mm)

    out: List[Point] = [start_point]
    traveled = 0.0
    for index in range(1, len(points) - 1):
        traveled += length(sub(points[index], points[index - 1]))
        if trim_start_mm < traveled < total - trim_end_mm:
            out.append(points[index])
    out.append(end_point)

    deduped: List[Point] = []
    for point in out:
        if not deduped or length(sub(point, deduped[-1])) > 1e-6:
            deduped.append(point)
    return deduped


def offset_polyline(points: Sequence[Point], offset_mm: float) -> List[Point]:
    if len(points) < 2:
        raise ValueError("polyline needs at least two points")

    tangents: List[Vector] = []
    normals: List[Vector] = []
    for start, end in zip(points, points[1:]):
        tangent = unit(sub(end, start))
        tangents.append(tangent)
        normals.append(left_normal(tangent))

    out: List[Point] = []
    out.append(add(points[0], mul(normals[0], offset_mm)))

    for index in range(1, len(points) - 1):
        prev_point = add(points[index], mul(normals[index - 1], offset_mm))
        next_point = add(points[index], mul(normals[index], offset_mm))
        intersection = line_intersection(prev_point, tangents[index - 1], next_point, tangents[index])
        if intersection is None:
            intersection = ((prev_point[0] + next_point[0]) / 2, (prev_point[1] + next_point[1]) / 2)
        out.append(intersection)

    out.append(add(points[-1], mul(normals[-1], offset_mm)))
    return out


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


def format_point(point: Point) -> str:
    return f"({point[0]:.3f}, {point[1]:.3f})"


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
            merged_start = add(origin, mul(direction, start))
            merged_end = add(origin, mul(direction, end))
            merged.append((merged_start, merged_end))

    return merged


def find_route_segments(
    board: "pcbnew.BOARD",
    construction_layer: str,
    group_name: str,
    tolerance_mm: float,
) -> List[Segment]:
    layer_id = get_layer_id(board, construction_layer)
    segments: List[Segment] = []
    ignored_in_group: List[str] = []

    for drawing in board.GetDrawings():
        if drawing.GetLayer() != layer_id:
            continue
        if get_group_name(drawing) != group_name:
            continue
        if type(drawing).__name__ != "PCB_SHAPE":
            ignored_in_group.append(getattr(drawing, "GetShapeStr", lambda: type(drawing).__name__)())
            continue

        start = from_vec(drawing.GetStart())
        end = from_vec(drawing.GetEnd())
        if drawing.GetShape() == pcbnew.SHAPE_T_SEGMENT and length(sub(end, start)) > 1e-6:
            segments.append((start, end))
        else:
            ignored_in_group.append(drawing.GetShapeStr())

    if ignored_in_group:
        print(
            f"warning: ignored {len(ignored_in_group)} non-line construction item(s) in {group_name!r}: "
            + ", ".join(sorted(set(str(item) for item in ignored_in_group))),
            file=sys.stderr,
        )
    if not segments:
        raise ValueError(
            f"no graphic line segments found on layer {construction_layer!r} in group {group_name!r}"
        )
    return merge_collinear_segments(segments, tolerance_mm)


def point_key(point: Point, tolerance_mm: float) -> Tuple[int, int]:
    return (round(point[0] / tolerance_mm), round(point[1] / tolerance_mm))


def order_segments(segments: Sequence[Segment], tolerance_mm: float) -> List[Point]:
    points_by_key: Dict[Tuple[int, int], List[Point]] = {}
    adjacency: Dict[Tuple[int, int], List[Tuple[Tuple[int, int], int]]] = {}

    for index, (start, end) in enumerate(segments):
        start_key = point_key(start, tolerance_mm)
        end_key = point_key(end, tolerance_mm)
        points_by_key.setdefault(start_key, []).append(start)
        points_by_key.setdefault(end_key, []).append(end)
        adjacency.setdefault(start_key, []).append((end_key, index))
        adjacency.setdefault(end_key, []).append((start_key, index))

    branches = [key for key, edges in adjacency.items() if len(edges) > 2]
    if branches:
        raise ValueError("construction route branches are not supported")

    endpoints = [key for key, edges in adjacency.items() if len(edges) == 1]

    def canonical(key: Tuple[int, int]) -> Point:
        pts = points_by_key[key]
        return (sum(point[0] for point in pts) / len(pts), sum(point[1] for point in pts) / len(pts))

    if len(endpoints) != 2:
        endpoint_text = ", ".join(format_point(canonical(key)) for key in endpoints)
        raise ValueError(
            f"construction route must be one open polyline; found {len(endpoints)} endpoint(s): "
            f"{endpoint_text}"
        )

    current = endpoints[0]
    used_segments = set()
    ordered = [canonical(current)]

    while len(used_segments) < len(segments):
        choices = [(next_key, seg_index) for next_key, seg_index in adjacency[current] if seg_index not in used_segments]
        if not choices:
            break
        if len(choices) > 1:
            raise ValueError("ambiguous construction route traversal")
        next_key, seg_index = choices[0]
        used_segments.add(seg_index)
        ordered.append(canonical(next_key))
        current = next_key

    if len(used_segments) != len(segments):
        raise ValueError("construction route has disconnected line segments")
    return ordered


def route_refs(route: Dict[str, Any]) -> List[str]:
    refs = expand_refs(route.get("refs", []))
    if len(refs) % 2:
        raise ValueError("'refs' must contain an even number of LED references split across two directions")
    return [str(ref) for ref in refs]


def parse_ref(ref: str) -> Tuple[str, int, int]:
    match = REF_RE.match(ref)
    if not match:
        raise ValueError(f"reference {ref!r} does not end in a numeric suffix")
    prefix, number = match.groups()
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
    return [
        f"{start_prefix}{number:0{width}d}"
        for number in range(start_number, end_number + step, step)
    ]


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


def get_segment_override(route: Dict[str, Any], index: int) -> Dict[str, Any]:
    overrides = route.get("segments", {})
    if not isinstance(overrides, dict):
        raise ValueError("'segments' overrides must be an object keyed by segment index")
    return dict(overrides.get(str(index), overrides.get(index, {})))


def candidate_led_positions(route: Dict[str, Any], points: Sequence[Point]) -> List[Tuple[int, Point, Vector, float]]:
    led_spacing = float(route["led_spacing_along_mm"])
    default_clearance = float(route.get("endpoint_clearance_mm", 0.0))
    candidates: List[Tuple[int, Point, Vector, float]] = []

    for index, (start, end) in enumerate(zip(points, points[1:])):
        seg_vec = sub(end, start)
        seg_len = length(seg_vec)
        if seg_len <= 1e-9:
            continue
        tangent = unit(seg_vec)
        override = get_segment_override(route, index)
        if override.get("no_leds", False):
            continue

        start_clearance = float(override.get("start_clearance_mm", default_clearance))
        end_clearance = float(override.get("end_clearance_mm", default_clearance))
        usable_len = seg_len - start_clearance - end_clearance
        if usable_len <= 1e-9:
            continue

        if "led_count" in override:
            count = int(override["led_count"])
        else:
            count = int(math.floor(usable_len / led_spacing + 1e-9))

        if count <= 0:
            continue

        if count == 1:
            distances = [start_clearance + usable_len / 2.0]
        else:
            span = (count - 1) * led_spacing
            if span > usable_len + 1e-9:
                raise ValueError(
                    f"segment {index} asks for {count} LED position(s), but only {usable_len:.3f} mm is usable"
                )
            margin = (usable_len - span) / 2.0
            distances = [start_clearance + margin + led_index * led_spacing for led_index in range(count)]

        offset = float(override.get("center_offset_along_mm", 0.0))
        for distance_on_segment in distances:
            distance_on_segment += offset
            if distance_on_segment < -1e-9 or distance_on_segment > seg_len + 1e-9:
                raise ValueError(f"segment {index} center_offset_along_mm places an LED outside the segment")
            center = add(start, mul(tangent, distance_on_segment))
            candidates.append((index, center, tangent, angle_degrees(tangent)))

    return candidates


def apply_led_override(route: Dict[str, Any], ref: str, point: Point, angle_deg: float) -> Tuple[Point, float]:
    override = dict(route.get("led_overrides", {}).get(ref, {}))
    if not override:
        return point, angle_deg

    if "nudge_mm" in override:
        nudge = override["nudge_mm"]
        if not isinstance(nudge, list) or len(nudge) != 2:
            raise ValueError(f"led_overrides.{ref}.nudge_mm must be [dx, dy]")
        point = (point[0] + float(nudge[0]), point[1] + float(nudge[1]))

    if "rotation_deg" in override:
        angle_deg = float(override["rotation_deg"])
    if "rotation_offset_deg" in override:
        angle_deg += float(override["rotation_offset_deg"])
    return point, angle_deg


def normalize_angle(angle: float) -> float:
    while angle > 180:
        angle -= 360
    while angle <= -180:
        angle += 360
    return angle


def build_led_placements(
    route_name: str,
    route: Dict[str, Any],
    points: Sequence[Point],
) -> List[LedPlacement]:
    refs = route_refs(route)
    refs_per_direction = len(refs) // 2
    first_direction_refs = refs[:refs_per_direction]
    second_direction_refs = refs[refs_per_direction:]
    candidates = candidate_led_positions(route, points)
    strict = bool(route.get("strict_led_count", True))

    if strict and len(candidates) != refs_per_direction:
        raise ValueError(
            f"route {route_name!r} has {refs_per_direction} LED(s) per direction, but construction geometry "
            f"generated {len(candidates)} position(s)"
        )
    if len(candidates) < refs_per_direction:
        raise ValueError(
            f"route {route_name!r} has {refs_per_direction} LED(s) per direction, but only {len(candidates)} "
            "position(s) fit on the construction geometry"
        )
    if len(candidates) > refs_per_direction:
        print(
            f"warning: route {route_name!r} generated {len(candidates)} LED position(s); "
            f"using the first {refs_per_direction} because strict_led_count is false",
            file=sys.stderr,
        )
        candidates = candidates[:refs_per_direction]

    across = float(route["line_spacing_across_mm"])
    first_direction_side = str(route.get("first_direction_side", "left")).lower()
    if first_direction_side not in ("left", "right"):
        raise ValueError("first_direction_side must be 'left' or 'right'")
    first_sign = 1.0 if first_direction_side == "left" else -1.0
    rotation_offset = float(route.get("led_rotation_offset_deg", 0.0))
    mirror_for_power = bool(route.get("mirror_led_rotations_for_power", True))

    def angle_for_side(base_angle: float, side_sign: float) -> float:
        angle = base_angle + rotation_offset
        if mirror_for_power and side_sign > 0:
            angle += 180.0
        return normalize_angle(angle)

    placements: List[LedPlacement] = []

    for index, ((segment_index, center, tangent, angle_deg), ref) in enumerate(
        zip(candidates, first_direction_refs)
    ):
        normal = left_normal(tangent)
        pos = add(center, mul(normal, first_sign * across / 2.0))
        angle = angle_for_side(angle_deg, first_sign)
        pos, angle = apply_led_override(route, ref, pos, angle)
        placements.append(
            LedPlacement(
                route_name=route_name,
                direction="first",
                index=index,
                segment_index=segment_index,
                ref=ref,
                pos=pos,
                angle_deg=angle,
            )
        )

    second_side_sign = -first_sign
    # The return direction is placed from route end back to route start, leaving
    # one data crossover at the far end instead of one at every station.
    for index, ((segment_index, center, tangent, angle_deg), ref) in enumerate(
        zip(reversed(candidates), second_direction_refs)
    ):
        normal = left_normal(tangent)
        pos = add(center, mul(normal, second_side_sign * across / 2.0))
        angle = angle_for_side(angle_deg, second_side_sign)
        pos, angle = apply_led_override(route, ref, pos, angle)
        placements.append(
            LedPlacement(
                route_name=route_name,
                direction="second",
                index=index,
                segment_index=segment_index,
                ref=ref,
                pos=pos,
                angle_deg=angle,
            )
        )
    return placements


def endpoint_spec_items(route: Dict[str, Any]) -> Iterable[Tuple[str, Dict[str, Any]]]:
    endpoints = route.get("endpoints", {})
    if isinstance(endpoints, dict):
        for name, spec in endpoints.items():
            if isinstance(spec, str):
                yield str(name), {"ref": spec}
            elif isinstance(spec, dict):
                yield str(name), dict(spec)
            else:
                raise ValueError("endpoint specs must be strings or objects")
    elif isinstance(endpoints, list):
        names = ["start", "end"]
        for index, ref in enumerate(endpoints):
            yield names[index] if index < len(names) else f"endpoint_{index}", {"ref": str(ref)}
    else:
        raise ValueError("'endpoints' must be an object or array")


def build_endpoint_placements(
    route_name: str,
    route: Dict[str, Any],
    points: Sequence[Point],
) -> List[EndpointPlacement]:
    placements: List[EndpointPlacement] = []
    first_tangent = unit(sub(points[1], points[0]))
    last_tangent = unit(sub(points[-1], points[-2]))

    for name, spec in endpoint_spec_items(route):
        if "ref" not in spec:
            raise ValueError(f"endpoint {name!r} is missing ref")
        endpoint_name = str(name)
        lower_name = endpoint_name.lower()
        if lower_name.startswith("start"):
            base_point = points[0]
            tangent = first_tangent
            direction_into_route = first_tangent
        elif lower_name.startswith("end"):
            base_point = points[-1]
            tangent = last_tangent
            direction_into_route = mul(last_tangent, -1.0)
        else:
            index = int(spec.get("point_index", 0))
            if index < 0:
                index += len(points)
            if index < 0 or index >= len(points):
                raise ValueError(f"endpoint {name!r} has invalid point_index {spec.get('point_index')}")
            base_point = points[index]
            if index == 0:
                tangent = first_tangent
                direction_into_route = first_tangent
            elif index == len(points) - 1:
                tangent = last_tangent
                direction_into_route = mul(last_tangent, -1.0)
            else:
                tangent = unit(sub(points[index + 1], points[index - 1]))
                direction_into_route = tangent

        offset_along = float(spec.get("offset_along_mm", 0.0))
        point = add(base_point, mul(direction_into_route, offset_along))
        if "nudge_mm" in spec:
            nudge = spec["nudge_mm"]
            if not isinstance(nudge, list) or len(nudge) != 2:
                raise ValueError(f"endpoint {name!r} nudge_mm must be [dx, dy]")
            point = (point[0] + float(nudge[0]), point[1] + float(nudge[1]))

        if lower_name.startswith("start"):
            endpoint_offset = float(route.get("endpoint_start_rotation_offset_deg", route.get("endpoint_rotation_offset_deg", 0.0)))
        elif lower_name.startswith("end"):
            endpoint_offset = float(route.get("endpoint_end_rotation_offset_deg", route.get("endpoint_rotation_offset_deg", 0.0)))
        else:
            endpoint_offset = float(route.get("endpoint_rotation_offset_deg", 0.0))
        angle = angle_degrees(tangent) + endpoint_offset
        orientation = str(spec.get("orientation", route.get("endpoint_orientation", "along_route"))).lower()
        if orientation in ("along_route", "route", "tangent"):
            pass
        elif orientation in ("perpendicular", "perpendicular_to_route"):
            angle += 90.0
        else:
            raise ValueError(
                f"endpoint {name!r} has invalid orientation {orientation!r}; "
                "use 'along_route' or 'perpendicular_to_route'"
            )
        if "rotation_deg" in spec:
            angle = float(spec["rotation_deg"])
        if "rotation_offset_deg" in spec:
            angle += float(spec["rotation_offset_deg"])

        point, angle = apply_led_override(route, str(spec["ref"]), point, angle)
        placements.append(EndpointPlacement(route_name, endpoint_name, str(spec["ref"]), point, angle))
    return placements


def set_footprint_pose(board: "pcbnew.BOARD", ref: str, pos: Point, angle_deg: float) -> None:
    footprint = board.FindFootprintByReference(ref)
    if footprint is None:
        raise ValueError(f"footprint {ref!r} not found")
    footprint.SetPosition(to_vec(pos))
    footprint.SetOrientationDegrees(angle_deg)


def route_led_refs(
    led_placements: Sequence[LedPlacement],
    endpoint_placements: Sequence[EndpointPlacement],
) -> List[str]:
    refs: List[str] = []
    seen = set()

    def add_ref(ref: str) -> None:
        if ref not in seen:
            seen.add(ref)
            refs.append(ref)

    for placement in led_placements:
        add_ref(placement.ref)
    for endpoint in endpoint_placements:
        add_ref(endpoint.ref)
    return refs


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


def hide_route_reference_designators(
    board: "pcbnew.BOARD",
    led_placements: Sequence[LedPlacement],
    endpoint_placements: Sequence[EndpointPlacement],
) -> Tuple[int, int]:
    refs = route_led_refs(led_placements, endpoint_placements)
    changed = sum(1 for ref in refs if hide_footprint_reference(board, ref))
    return changed, len(refs)


def delete_generated_group(board: "pcbnew.BOARD", group_name: str) -> None:
    for group in list(board.Groups()):
        if group.GetName() != group_name:
            continue
        for item in list(group.GetItems()):
            group.RemoveItem(item)
            board.Delete(item)
        board.Delete(group)


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


def add_generated_segment(
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


def add_generated_arc(
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


def silk_commands_for_route(route: Dict[str, Any], points: Sequence[Point]) -> List[SilkCommand]:
    gap_mm = float(route["silk_parallel_gap_mm"])
    radius_mm = float(route.get("silk_fillet_radius_mm", DEFAULTS["silk_fillet_radius_mm"]))
    start_trim = float(route.get("silk_start_trim_mm", 0.0))
    end_trim = float(route.get("silk_end_trim_mm", 0.0))
    silk_points = trim_polyline(points, start_trim, end_trim)

    commands: List[SilkCommand] = []
    for offset in (gap_mm / 2.0, -gap_mm / 2.0):
        offset_points = offset_polyline(silk_points, offset)
        commands.extend(fillet_polyline(offset_points, radius_mm))
    return commands


def generate_silk(board: "pcbnew.BOARD", route_name: str, route: Dict[str, Any], points: Sequence[Point]) -> int:
    generated_group_name = f"routegen:{route_name}"
    delete_generated_group(board, generated_group_name)

    if not bool(route.get("generate_silk", True)):
        return 0

    layer_id = get_layer_id(board, str(route.get("silk_layer", DEFAULTS["silk_layer"])))
    width_mm = float(route["silk_width_mm"])
    commands = silk_commands_for_route(route, points)

    group = pcbnew.PCB_GROUP(board)
    group.SetName(generated_group_name)
    board.Add(group)

    for command, start, mid, end in commands:
        if command == "line":
            add_generated_segment(board, group, layer_id, start, end, width_mm)
        elif command == "arc" and mid is not None:
            add_generated_arc(board, group, layer_id, start, mid, end, width_mm)
        else:
            raise ValueError(f"unknown silk command {command!r}")
    return len(commands)


def route_points_from_config(
    board: "pcbnew.BOARD",
    route_name: str,
    route: Dict[str, Any],
) -> List[Point]:
    if "polyline_mm" in route:
        points = [(float(point[0]), float(point[1])) for point in route["polyline_mm"]]
    else:
        group_name = str(route.get("construction_group", f"route:{route_name}"))
        tolerance_mm = float(route["connection_tolerance_mm"])
        segments = find_route_segments(board, str(route["construction_layer"]), group_name, tolerance_mm)
        points = order_segments(segments, tolerance_mm)

    if bool(route.get("reverse", False)):
        points = list(reversed(points))
    if len(points) < 2:
        raise ValueError(f"route {route_name!r} needs at least two points")
    return points


def report_route(
    route_name: str,
    points: Sequence[Point],
    led_placements: Sequence[LedPlacement],
    endpoint_placements: Sequence[EndpointPlacement],
    generated_silk_count: Optional[int],
    hidden_reference_count: Optional[Tuple[int, int]] = None,
) -> None:
    total_len = sum(length(sub(end, start)) for start, end in zip(points, points[1:]))
    print(f"\nroute {route_name}:")
    print(f"  points: {len(points)}  segments: {len(points) - 1}  length: {total_len:.3f} mm")
    for index, (start, end) in enumerate(zip(points, points[1:])):
        seg = sub(end, start)
        print(
            f"  segment {index}: {length(seg):.3f} mm at {angle_degrees(seg):.1f} deg "
            f"from ({start[0]:.3f}, {start[1]:.3f}) to ({end[0]:.3f}, {end[1]:.3f})"
        )
    first_count = sum(1 for placement in led_placements if placement.direction == "first")
    second_count = sum(1 for placement in led_placements if placement.direction == "second")
    print(f"  route LEDs: {len(led_placements)} ({first_count} first direction, {second_count} second direction)")
    for placement in led_placements:
        print(
            f"    {placement.direction} {placement.index}: seg {placement.segment_index}: "
            f"{placement.ref} ({placement.pos[0]:.3f}, {placement.pos[1]:.3f}) "
            f"rot {placement.angle_deg:.1f}"
        )
    if endpoint_placements:
        print(f"  endpoints: {len(endpoint_placements)}")
        for endpoint in endpoint_placements:
            print(
                f"    {endpoint.name}: {endpoint.ref} "
                f"({endpoint.pos[0]:.3f}, {endpoint.pos[1]:.3f}) rot {endpoint.angle_deg:.1f}"
            )
    if generated_silk_count is not None:
        print(f"  generated silkscreen items: {generated_silk_count}")
    if hidden_reference_count is not None:
        changed, total = hidden_reference_count
        print(f"  hid reference designators: {changed} changed / {total} route LEDs")


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as config_file:
        config = json.load(config_file)
    if not isinstance(config, dict):
        raise ValueError("config root must be a JSON object")
    if "routes" not in config or not isinstance(config["routes"], dict):
        raise ValueError("config must contain a 'routes' object")
    return config


def selected_routes(config: Dict[str, Any], names: Sequence[str]) -> Iterable[Tuple[str, Dict[str, Any]]]:
    defaults = deep_merge(DEFAULTS, dict(config.get("defaults", {})))
    if names:
        missing = [name for name in names if name not in config["routes"]]
        if missing:
            raise ValueError(f"route(s) not found in config: {', '.join(missing)}")
        route_names = names
    else:
        route_names = list(config["routes"].keys())

    for route_name in route_names:
        route = deep_merge(defaults, dict(config["routes"][route_name]))
        yield route_name, route


def check_lock_file(board_path: str, output_path: Optional[str], force: bool) -> None:
    if output_path and os.path.abspath(output_path) != os.path.abspath(board_path):
        return
    lock_path = os.path.join(os.path.dirname(board_path), f"~{os.path.basename(board_path)}.lck")
    if os.path.exists(lock_path) and not force:
        raise ValueError(
            f"KiCad lock file exists: {lock_path}. Close the board, use --output for a copy, "
            "or pass --force if you intentionally want to write anyway."
        )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", default="transit.kicad_pcb", help="KiCad board file to read")
    parser.add_argument("--config", required=True, help="route JSON config file")
    parser.add_argument("--route", action="append", default=[], help="only process this route; repeatable")
    parser.add_argument("--apply", action="store_true", help="write footprint and silkscreen changes")
    parser.add_argument("--output", help="write to this board path instead of overwriting --board")
    parser.add_argument("--force", action="store_true", help="allow in-place writes while a KiCad lock file exists")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    board = pcbnew.LoadBoard(args.board)
    config = load_config(args.config)

    processed = 0
    for route_name, route in selected_routes(config, args.route):
        points = route_points_from_config(board, route_name, route)
        led_placements = build_led_placements(route_name, route, points)
        endpoint_placements = build_endpoint_placements(route_name, route, points)

        generated_silk_count: Optional[int] = None
        hidden_reference_count: Optional[Tuple[int, int]] = None
        if args.apply:
            for placement in led_placements:
                set_footprint_pose(
                    board,
                    placement.ref,
                    placement.pos,
                    placement.angle_deg,
                )
            for endpoint in endpoint_placements:
                set_footprint_pose(board, endpoint.ref, endpoint.pos, endpoint.angle_deg)
            generated_silk_count = generate_silk(board, route_name, route, points)
            hidden_reference_count = hide_route_reference_designators(
                board,
                led_placements,
                endpoint_placements,
            )
        else:
            if bool(route.get("generate_silk", True)):
                generated_silk_count = len(silk_commands_for_route(route, points))

        report_route(
            route_name,
            points,
            led_placements,
            endpoint_placements,
            generated_silk_count,
            hidden_reference_count,
        )
        processed += 1

    if args.apply:
        output_path = args.output or args.board
        check_lock_file(args.board, args.output, args.force)
        pcbnew.SaveBoard(output_path, board)
        print(f"\nwrote {output_path}")
    else:
        print("\ndry run only; rerun with --apply to write changes")

    print(f"processed {processed} route(s)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
