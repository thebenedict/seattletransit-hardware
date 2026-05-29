from __future__ import annotations

from dataclasses import dataclass
from math import ceil, cos, floor, radians, sin, sqrt
from typing import Iterable, Optional, Sequence, Tuple


Point = Tuple[float, float]


@dataclass(frozen=True)
class Box:
    """Axis-aligned box in board millimeters."""

    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def __post_init__(self) -> None:
        if self.max_x < self.min_x or self.max_y < self.min_y:
            raise ValueError(f"invalid box: {self}")

    @classmethod
    def from_points(cls, points: Iterable[Point]) -> "Box":
        pts = list(points)
        if not pts:
            raise ValueError("at least one point is required")
        xs = [point[0] for point in pts]
        ys = [point[1] for point in pts]
        return cls(min(xs), min(ys), max(xs), max(ys))

    @classmethod
    def union(cls, boxes: Sequence["Box"]) -> "Box":
        if not boxes:
            raise ValueError("at least one box is required")
        return cls(
            min(box.min_x for box in boxes),
            min(box.min_y for box in boxes),
            max(box.max_x for box in boxes),
            max(box.max_y for box in boxes),
        )

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y

    @property
    def center(self) -> Point:
        return ((self.min_x + self.max_x) / 2.0, (self.min_y + self.max_y) / 2.0)

    def inflate(self, amount: float) -> "Box":
        return Box(
            self.min_x - amount,
            self.min_y - amount,
            self.max_x + amount,
            self.max_y + amount,
        )

    def inflate_xy(self, x_amount: float, y_amount: float) -> "Box":
        return Box(
            self.min_x - x_amount,
            self.min_y - y_amount,
            self.max_x + x_amount,
            self.max_y + y_amount,
        )

    def move(self, dx: float, dy: float) -> "Box":
        return Box(self.min_x + dx, self.min_y + dy, self.max_x + dx, self.max_y + dy)

    def snap_outward(self, grid_mm: Optional[float]) -> "Box":
        if not grid_mm or grid_mm <= 0:
            return self
        return Box(
            floor(self.min_x / grid_mm) * grid_mm,
            floor(self.min_y / grid_mm) * grid_mm,
            ceil(self.max_x / grid_mm) * grid_mm,
            ceil(self.max_y / grid_mm) * grid_mm,
        )


def normalize_side(side: Optional[str]) -> str:
    if not side:
        return "E"
    normalized = side.strip().upper().replace("-", "_")
    aliases = {
        "TOP": "N",
        "UP": "N",
        "ABOVE": "N",
        "BOTTOM": "S",
        "DOWN": "S",
        "BELOW": "S",
        "LEFT": "W",
        "RIGHT": "E",
        "CENTER": "C",
        "CENTRE": "C",
        "MIDDLE": "C",
    }
    return aliases.get(normalized, normalized)


def resolve_label_alignment(
    side: Optional[str],
    align: Optional[str],
    style: Optional[str] = None,
) -> str:
    """Resolve label horizontal alignment to left, center, or right."""

    style_name = (style or "plain").strip().lower()
    align_name = (align or "auto").strip().lower().replace("-", "_")
    aliases = {
        "centre": "center",
        "middle": "center",
        "start": "auto",
        "outside": "auto",
    }
    align_name = aliases.get(align_name, align_name)

    if align_name == "auto":
        if style_name == "knockout":
            return "center"

        side_name = normalize_side(side)
        if "E" in side_name:
            return "left"
        if "W" in side_name:
            return "right"
        return "center"

    if align_name not in {"left", "center", "right"}:
        raise ValueError(f"unknown label alignment {align!r}")

    return align_name


def rounded_box_points(box: Box, radius: float, segments: int = 5) -> Tuple[Point, ...]:
    """Return clockwise points approximating a rounded rectangle in y-down coordinates."""

    if box.width == 0 or box.height == 0:
        return (
            (box.min_x, box.min_y),
            (box.max_x, box.min_y),
            (box.max_x, box.max_y),
            (box.min_x, box.max_y),
        )

    r = max(0.0, min(radius, box.width / 2.0, box.height / 2.0))
    if r == 0:
        return (
            (box.max_x, box.min_y),
            (box.max_x, box.max_y),
            (box.min_x, box.max_y),
            (box.min_x, box.min_y),
        )

    segment_count = max(1, int(segments))
    points = []
    arcs = (
        ((box.max_x - r, box.min_y + r), -90.0, 0.0),
        ((box.max_x - r, box.max_y - r), 0.0, 90.0),
        ((box.min_x + r, box.max_y - r), 90.0, 180.0),
        ((box.min_x + r, box.min_y + r), 180.0, 270.0),
    )

    for center, start, end in arcs:
        cx, cy = center
        for index in range(segment_count + 1):
            angle = radians(start + ((end - start) * index / segment_count))
            points.append((cx + (r * cos(angle)), cy + (r * sin(angle))))

    return tuple(points)


def box_points(box: Box) -> Tuple[Point, ...]:
    return (
        (box.min_x, box.min_y),
        (box.max_x, box.min_y),
        (box.max_x, box.max_y),
        (box.min_x, box.max_y),
    )


def circle_points(center: Point, radius: float, segments: int = 32) -> Tuple[Point, ...]:
    segment_count = max(8, int(segments))
    r = max(0.0, float(radius))
    return tuple(
        (
            center[0] + (r * cos((2.0 * 3.141592653589793 * index) / segment_count)),
            center[1] + (r * sin((2.0 * 3.141592653589793 * index) / segment_count)),
        )
        for index in range(segment_count)
    )


def rotate_points(points: Sequence[Point], angle_deg: float) -> Tuple[Point, ...]:
    if angle_deg % 360 == 0:
        return tuple(points)

    angle = radians(angle_deg)
    c = cos(angle)
    s = sin(angle)
    return tuple((x * c + y * s, -x * s + y * c) for x, y in points)


def rotate_points_around(points: Sequence[Point], angle_deg: float, origin: Point) -> Tuple[Point, ...]:
    translated = tuple((x - origin[0], y - origin[1]) for x, y in points)
    return tuple((x + origin[0], y + origin[1]) for x, y in rotate_points(translated, angle_deg))


def oriented_rounded_box_points(
    content_points: Sequence[Point],
    angle_deg: float,
    padding_mm: float,
    radius_mm: float,
    snap_mm: Optional[float],
) -> Tuple[Point, ...]:
    if not content_points:
        raise ValueError("content_points must not be empty")

    origin = Box.from_points(content_points).center
    local_points = rotate_points_around(content_points, -angle_deg, origin)
    local_box = Box.from_points(local_points).inflate(padding_mm).snap_outward(snap_mm)
    local_rounded_points = rounded_box_points(local_box, radius_mm)
    return rotate_points_around(local_rounded_points, angle_deg, origin)


def rotated_box_points(box: Box, angle_deg: float) -> Tuple[Point, ...]:
    return rotate_points(box_points(box), angle_deg)


def translate_points(points: Sequence[Point], offset: Point) -> Tuple[Point, ...]:
    return tuple((x + offset[0], y + offset[1]) for x, y in points)


def side_direction(side: Optional[str]) -> Point:
    side_name = normalize_side(side)
    dx = 0.0
    dy = 0.0
    if "E" in side_name:
        dx += 1.0
    if "W" in side_name:
        dx -= 1.0
    if "S" in side_name:
        dy += 1.0
    if "N" in side_name:
        dy -= 1.0
    if dx == 0.0 and dy == 0.0:
        return (0.0, 0.0)
    length = sqrt((dx * dx) + (dy * dy))
    return (dx / length, dy / length)


def project(point: Point, axis: Point) -> float:
    return (point[0] * axis[0]) + (point[1] * axis[1])


def projection_range(points: Sequence[Point], axis: Point) -> Tuple[float, float]:
    values = [project(point, axis) for point in points]
    return (min(values), max(values))


def place_shape_against_anchor(
    anchor_points: Sequence[Point],
    relative_points: Sequence[Point],
    side: Optional[str],
    offset_mm: float,
    nudge_mm: Point = (0.0, 0.0),
    explicit_position_mm: Optional[Point] = None,
    cross_align: Optional[str] = None,
    align_x: Optional[str] = None,
    align_y: Optional[str] = None,
) -> Point:
    """Place a relative rendered shape with projected clearance from an anchor.

    The returned point is the translation for ``relative_points``.  Unlike
    axis-aligned box placement, the clearance is measured along the requested
    side direction against the rendered, already-rotated shape.
    """

    if explicit_position_mm is not None:
        return (
            explicit_position_mm[0] + nudge_mm[0],
            explicit_position_mm[1] + nudge_mm[1],
        )
    if not anchor_points:
        raise ValueError("anchor_points must not be empty")
    if not relative_points:
        raise ValueError("relative_points must not be empty")

    direction = side_direction(side)
    anchor_box = Box.from_points(anchor_points)
    relative_box = Box.from_points(relative_points)

    if direction == (0.0, 0.0):
        x = anchor_box.center[0] - relative_box.center[0]
        y = anchor_box.center[1] - relative_box.center[1]
    else:
        _anchor_normal_min, normal_max = projection_range(anchor_points, direction)
        relative_normal_min, _relative_normal_max = projection_range(relative_points, direction)
        normal_delta = normal_max + offset_mm - relative_normal_min

        tangent = (-direction[1], direction[0])
        relative_tangent_min, relative_tangent_max = projection_range(relative_points, tangent)
        anchor_tangent_target = cross_alignment_target(anchor_points, tangent, cross_align)
        tangent_delta = (
            anchor_tangent_target
            - ((relative_tangent_min + relative_tangent_max) / 2.0)
        )

        x = (direction[0] * normal_delta) + (tangent[0] * tangent_delta)
        y = (direction[1] * normal_delta) + (tangent[1] * tangent_delta)

    align_x_name = normalize_edge_alignment(align_x)
    align_y_name = normalize_edge_alignment(align_y)
    if align_x_name == "min":
        x = anchor_box.min_x - relative_box.min_x
    elif align_x_name == "max":
        x = anchor_box.max_x - relative_box.max_x
    elif align_x_name == "center":
        x = anchor_box.center[0] - relative_box.center[0]

    if align_y_name == "min":
        y = anchor_box.min_y - relative_box.min_y
    elif align_y_name == "max":
        y = anchor_box.max_y - relative_box.max_y
    elif align_y_name == "center":
        y = anchor_box.center[1] - relative_box.center[1]

    return (x + nudge_mm[0], y + nudge_mm[1])


def cross_alignment_target(points: Sequence[Point], tangent: Point, value: Optional[str]) -> float:
    tangent_min, tangent_max = projection_range(points, tangent)
    normalized = normalize_cross_alignment(value)

    if normalized is None or normalized == "center":
        return (tangent_min + tangent_max) / 2.0
    if normalized == "min":
        return tangent_min
    if normalized == "max":
        return tangent_max

    if normalized in {"top", "bottom"}:
        y_target = min(point[1] for point in points) if normalized == "top" else max(point[1] for point in points)
        candidates = [point for point in points if abs(point[1] - y_target) < 1e-9]
    else:
        x_target = min(point[0] for point in points) if normalized == "left" else max(point[0] for point in points)
        candidates = [point for point in points if abs(point[0] - x_target) < 1e-9]

    candidate_min, candidate_max = projection_range(candidates, tangent)
    return (candidate_min + candidate_max) / 2.0


def normalize_cross_alignment(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None

    normalized = value.strip().lower().replace("-", "_")
    aliases = {
        "centre": "center",
        "middle": "center",
        "center": "center",
        "min": "min",
        "start": "min",
        "max": "max",
        "end": "max",
        "top": "top",
        "upper": "top",
        "bottom": "bottom",
        "lower": "bottom",
        "left": "left",
        "right": "right",
    }
    if normalized not in aliases:
        raise ValueError(f"unknown cross alignment {value!r}")
    return aliases[normalized]


def normalize_edge_alignment(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None

    normalized = value.strip().lower().replace("-", "_")
    aliases = {
        "left": "min",
        "top": "min",
        "start": "min",
        "min": "min",
        "right": "max",
        "bottom": "max",
        "end": "max",
        "max": "max",
        "center": "center",
        "centre": "center",
        "middle": "center",
    }
    if normalized not in aliases:
        raise ValueError(f"unknown edge alignment {value!r}")
    return aliases[normalized]
