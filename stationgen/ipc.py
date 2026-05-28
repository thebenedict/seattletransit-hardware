from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from stationgen.capture import (
    build_station_entry,
    normalize_label_text,
    station_id_from_label,
    upsert_station_config,
)
from stationgen.config import deep_merge, find_default_config, load_config, station_entries
from stationgen.geometry import (
    Box,
    Point,
    box_points,
    circle_points,
    place_shape_against_anchor,
    resolve_label_alignment,
    rounded_box_points,
    rotated_box_points,
)


def _require_kipy():
    try:
        import kipy
        from kipy import KiCad
        from kipy.board_types import BoardRectangle, BoardText, FootprintInstance, Group, Zone
        from kipy.board_types import IslandRemovalMode, ZoneBorderStyle, ZoneFillMode, ZoneType
        from kipy.client import ApiError
        from kipy.common_types import Text
        from kipy.errors import ConnectionError as KipyConnectionError
        from kipy.geometry import Angle, PolyLineNode, PolygonWithHoles, Vector2
        from kipy.proto.common import types as common_types
        from kipy.proto.common.types import KiCadObjectType
        from kipy.proto.board.board_types_pb2 import BoardLayer
        from kipy.util.board_layer import layer_from_canonical_name
        from kipy.util.units import from_mm, to_mm
    except ImportError as exc:
        raise RuntimeError(
            "StationGen requires the official kicad-python IPC bindings. "
            "Install the plugin requirements or run `python -m pip install kicad-python`."
        ) from exc

    return {
        "kipy": kipy,
        "KiCad": KiCad,
        "BoardRectangle": BoardRectangle,
        "BoardText": BoardText,
        "FootprintInstance": FootprintInstance,
        "Group": Group,
        "Zone": Zone,
        "IslandRemovalMode": IslandRemovalMode,
        "ZoneBorderStyle": ZoneBorderStyle,
        "ZoneFillMode": ZoneFillMode,
        "ZoneType": ZoneType,
        "ApiError": ApiError,
        "KipyConnectionError": KipyConnectionError,
        "KiCadObjectType": KiCadObjectType,
        "Angle": Angle,
        "PolyLineNode": PolyLineNode,
        "PolygonWithHoles": PolygonWithHoles,
        "Text": Text,
        "Vector2": Vector2,
        "common_types": common_types,
        "BoardLayer": BoardLayer,
        "layer_from_canonical_name": layer_from_canonical_name,
        "from_mm": from_mm,
        "to_mm": to_mm,
    }


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    raise ValueError(f"expected a string or list, got {type(value).__name__}")


def _clean_optional(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped or stripped in {"-", "(default)", "default"}:
        return None
    return stripped


def _as_mapping_list(value: Any) -> List[Mapping[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"expected a list, got {type(value).__name__}")
    out: List[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError(f"expected list entries to be mappings, got {type(item).__name__}")
        out.append(item)
    return out


class StationGenIPC:
    def __init__(self) -> None:
        self.k = _require_kipy()
        self.kicad = self.k["KiCad"](client_name="stationgen")
        self.board = self.kicad.get_board()
        self.needs_zone_refill = False
        self._wx_app = None

    def box_from_kipy(self, box: Any) -> Box:
        to_mm = self.k["to_mm"]
        pos = box.pos
        size = box.size
        return Box(
            to_mm(pos.x),
            to_mm(pos.y),
            to_mm(pos.x + size.x),
            to_mm(pos.y + size.y),
        )

    def vec(self, point: Point):
        return self.k["Vector2"].from_xy_mm(point[0], point[1])

    def layer_id(self, layer_name: str):
        layer = self.k["layer_from_canonical_name"](layer_name)
        if layer == self.k["BoardLayer"].BL_UNKNOWN:
            raise ValueError(f"unknown KiCad layer {layer_name!r}")
        return layer

    def footprints_by_ref(self) -> Dict[str, Any]:
        by_ref: Dict[str, Any] = {}
        for footprint in self.board.get_footprints():
            ref = footprint.reference_field.text.value
            by_ref[ref] = footprint
        return by_ref

    def selected_station_parts(self):
        selection = self.board.get_selection()
        footprints = [
            item for item in selection if isinstance(item, self.k["FootprintInstance"])
        ]
        texts = [item for item in selection if isinstance(item, self.k["BoardText"])]
        return footprints, texts

    def infer_side_from_point(self, anchor: Box, position: Point) -> str:
        horizontal = ""
        vertical = ""

        if position[0] < anchor.min_x:
            horizontal = "W"
        elif position[0] > anchor.max_x:
            horizontal = "E"

        if position[1] < anchor.min_y:
            vertical = "N"
        elif position[1] > anchor.max_y:
            vertical = "S"

        return f"{vertical}{horizontal}" or "E"

    def text_position_mm(self, text: Any) -> Point:
        to_mm = self.k["to_mm"]
        return (to_mm(text.position.x), to_mm(text.position.y))

    def horizontal_alignment_name(self, text: Any) -> Optional[str]:
        common_types = self.k["common_types"]
        alignment = text.attributes.horizontal_alignment
        return {
            common_types.HorizontalAlignment.HA_LEFT: "left",
            common_types.HorizontalAlignment.HA_CENTER: "center",
            common_types.HorizontalAlignment.HA_RIGHT: "right",
        }.get(alignment)

    def vertical_alignment_name(self, text: Any) -> Optional[str]:
        common_types = self.k["common_types"]
        alignment = text.attributes.vertical_alignment
        return {
            common_types.VerticalAlignment.VA_TOP: "top",
            common_types.VerticalAlignment.VA_CENTER: "center",
            common_types.VerticalAlignment.VA_BOTTOM: "bottom",
        }.get(alignment)

    def prompt_capture_options(self, defaults: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            import wx
        except ImportError as exc:
            raise RuntimeError(
                "Capture needs wxPython for the dialog. Run with --capture-no-dialog "
                "and explicit options instead."
            ) from exc

        self._wx_app = wx.GetApp()
        if self._wx_app is None:
            self._wx_app = wx.App(False)

        dialog = wx.Dialog(None, title="Capture StationGen Station")
        panel = wx.Panel(dialog)
        panel_sizer = wx.BoxSizer(wx.VERTICAL)
        panel.SetSizer(panel_sizer)

        refs_text = ", ".join(defaults["refs"])
        if len(refs_text) > 72:
            refs_text = f"{refs_text[:69]}..."
        panel_sizer.Add(wx.StaticText(panel, label=f"Selected LEDs: {refs_text}"), 0, wx.ALL, 8)

        grid = wx.FlexGridSizer(rows=0, cols=2, vgap=6, hgap=10)
        grid.AddGrowableCol(1, 1)
        panel_sizer.Add(grid, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 8)

        def add_row(label: str, control: Any) -> Any:
            grid.Add(wx.StaticText(panel, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            grid.Add(control, 1, wx.EXPAND)
            return control

        station_id_ctrl = add_row(
            "Station ID",
            wx.TextCtrl(panel, value=str(defaults.get("station_id", ""))),
        )
        label_ctrl = add_row(
            "Label text",
            wx.TextCtrl(
                panel,
                value=str(defaults.get("label_text", "")),
                style=wx.TE_MULTILINE | wx.TE_PROCESS_ENTER,
                size=(-1, 56),
            ),
        )

        class_ctrl = wx.Choice(panel, choices=["standard", "transfer", "terminal"])
        class_ctrl.SetStringSelection(str(defaults.get("station_class", "standard")))
        add_row("Class", class_ctrl)

        side_ctrl = wx.Choice(panel, choices=["N", "NE", "E", "SE", "S", "SW", "W", "NW", "C"])
        side_ctrl.SetStringSelection(str(defaults.get("side", "E")))
        add_row("Label side", side_ctrl)

        align_ctrl = wx.Choice(panel, choices=["auto", "left", "center", "right"])
        align_ctrl.SetStringSelection(str(defaults.get("align", "auto")))
        add_row("Text align", align_ctrl)

        align_x_ctrl = wx.Choice(panel, choices=["(default)", "left", "center", "right"])
        align_x_ctrl.SetStringSelection(str(defaults.get("align_x") or "(default)"))
        add_row("Edge align X", align_x_ctrl)

        align_y_ctrl = wx.Choice(panel, choices=["(default)", "top", "center", "bottom"])
        align_y_ctrl.SetStringSelection(str(defaults.get("align_y") or "(default)"))
        add_row("Edge align Y", align_y_ctrl)

        exact_position_ctrl = wx.CheckBox(panel, label="Use selected text anchor as exact position")
        exact_position_ctrl.SetValue(bool(defaults.get("exact_position", False)))
        exact_position_ctrl.Enable(bool(defaults.get("has_selected_text", False)))
        panel_sizer.Add(exact_position_ctrl, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        regenerate_ctrl = wx.CheckBox(panel, label="Regenerate this station after updating YAML")
        regenerate_ctrl.SetValue(True)
        panel_sizer.Add(regenerate_ctrl, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        dialog_sizer = wx.BoxSizer(wx.VERTICAL)
        dialog_sizer.Add(panel, 1, wx.EXPAND)
        buttons = dialog.CreateSeparatedButtonSizer(wx.OK | wx.CANCEL)
        if buttons is not None:
            dialog_sizer.Add(buttons, 0, wx.EXPAND | wx.ALL, 8)
        dialog.SetSizer(dialog_sizer)
        dialog.Fit()
        dialog.SetMinSize((440, dialog.GetSize().height))

        result = dialog.ShowModal()
        if result != wx.ID_OK:
            dialog.Destroy()
            return None

        values = {
            "station_id": station_id_ctrl.GetValue().strip(),
            "label_text": label_ctrl.GetValue().strip(),
            "station_class": class_ctrl.GetStringSelection(),
            "side": side_ctrl.GetStringSelection(),
            "align": align_ctrl.GetStringSelection(),
            "align_x": _clean_optional(align_x_ctrl.GetStringSelection()),
            "align_y": _clean_optional(align_y_ctrl.GetStringSelection()),
            "exact_position": exact_position_ctrl.GetValue(),
            "regenerate": regenerate_ctrl.GetValue(),
        }
        dialog.Destroy()
        return values

    def capture_selected_station(self, config_path: Path, args: argparse.Namespace) -> Optional[str]:
        footprints, texts = self.selected_station_parts()
        if not footprints:
            raise ValueError("select at least one LED footprint before capturing a station")

        refs = [footprint.reference_field.text.value for footprint in footprints]
        selected_text = texts[0] if texts else None
        label_text = args.label_text if args.label_text is not None else ""
        if not label_text and selected_text is not None:
            label_text = selected_text.value.strip()
        label_text = normalize_label_text(label_text)

        footprints_by_ref = {footprint.reference_field.text.value: footprint for footprint in footprints}
        core_box = self.station_core_box(refs, footprints_by_ref)

        selected_position = self.text_position_mm(selected_text) if selected_text is not None else None
        default_side = self.infer_side_from_point(core_box, selected_position) if selected_position else "E"
        default_align = self.horizontal_alignment_name(selected_text) if selected_text is not None else "auto"
        default_vertical_align = self.vertical_alignment_name(selected_text) if selected_text is not None else None
        default_station_id = (
            args.station_id
            or station_id_from_label(label_text)
            or station_id_from_label(refs[0])
        )
        default_station_class = args.station_class or (
            "terminal"
            if selected_text is not None and bool(getattr(selected_text.proto, "knockout", False))
            else "standard"
            if selected_text is not None
            else "transfer"
        )

        options: Dict[str, Any] = {
            "refs": refs,
            "station_id": default_station_id,
            "label_text": label_text,
            "station_class": default_station_class,
            "side": args.label_side or default_side,
            "align": args.label_align or default_align or "auto",
            "align_x": args.label_align_x,
            "align_y": args.label_align_y,
            "has_selected_text": selected_text is not None,
            "exact_position": bool(args.exact_label_position and selected_text is not None),
            "regenerate": bool(args.regenerate_after_capture),
        }

        if not args.capture_no_dialog:
            prompted = self.prompt_capture_options(options)
            if prompted is None:
                return None
            options.update(prompted)

        station_id = str(options["station_id"]).strip()
        if not station_id:
            raise ValueError("station id is required")
        station_id = station_id_from_label(station_id)
        if not station_id:
            raise ValueError("station id must contain at least one letter or number")

        exact_position = bool(options.get("exact_position") and selected_position is not None)
        entry = build_station_entry(
            station_class=str(options.get("station_class") or "transfer"),
            refs=refs,
            label_text=str(options.get("label_text") or ""),
            side=None if exact_position else str(options.get("side") or "E"),
            align=_clean_optional(str(options.get("align") or "")),
            vertical_align=default_vertical_align if exact_position else None,
            align_x=_clean_optional(str(options.get("align_x") or "")),
            align_y=_clean_optional(str(options.get("align_y") or "")),
            angle_deg=selected_text.attributes.angle if selected_text is not None else None,
            position_mm=selected_position if exact_position else None,
        )

        action = upsert_station_config(config_path, station_id, entry)
        if bool(options.get("regenerate")):
            config = load_config(config_path)
            self.regenerate(config, [station_id])
        return f"{action} {station_id} with {len(refs)} LED ref(s) in {config_path}"

    def generated_group_name(self, config: Mapping[str, Any], station_id: str) -> str:
        prefix = str(config.get("defaults", {}).get("generated_group_prefix", "stationgen:"))
        return f"{prefix}{station_id}"

    def delete_generated_group(self, group_name: str) -> None:
        groups = self.board.get_items(types=self.k["KiCadObjectType"].KOT_PCB_GROUP)
        for group in groups:
            if group.name != group_name:
                continue
            for item_id in list(getattr(group, "_item_ids", [])):
                try:
                    self.board.remove_items_by_id(item_id)
                except self.k["ApiError"] as exc:
                    if "none of the requested IDs were found or valid" not in str(exc):
                        raise
            self.board.remove_items_by_id(group.id)

    def make_text_attrs(self, spec: Mapping[str, Any]):
        common_types = self.k["common_types"]
        attrs = self.k["Text"]().attributes
        size_mm = float(spec.get("size_mm", 1.2))
        side_value = spec.get("side", "E")
        align_value = spec.get("align")
        alignment = resolve_label_alignment(
            str(side_value) if side_value is not None else None,
            str(align_value) if align_value is not None else None,
            str(spec.get("style", "plain")),
        )
        horizontal_alignment = {
            "left": common_types.HorizontalAlignment.HA_LEFT,
            "center": common_types.HorizontalAlignment.HA_CENTER,
            "right": common_types.HorizontalAlignment.HA_RIGHT,
        }[alignment]
        vertical_align = str(spec.get("vertical_align", "center")).strip().lower()
        vertical_align = {"centre": "center", "middle": "center"}.get(vertical_align, vertical_align)
        vertical_alignment = {
            "top": common_types.VerticalAlignment.VA_TOP,
            "center": common_types.VerticalAlignment.VA_CENTER,
            "bottom": common_types.VerticalAlignment.VA_BOTTOM,
        }.get(vertical_align)
        if vertical_alignment is None:
            raise ValueError(f"unknown label vertical alignment {vertical_align!r}")
        attrs.size = self.k["Vector2"].from_xy_mm(size_mm, size_mm)
        attrs.stroke_width = self.k["from_mm"](float(spec.get("stroke_mm", 0.15)))
        attrs.angle = float(spec.get("angle_deg", 0.0))
        attrs.horizontal_alignment = horizontal_alignment
        attrs.vertical_alignment = vertical_alignment
        is_multiline = bool(
            spec.get("multiline", False) or "\n" in normalize_label_text(str(spec.get("text", "")))
        )
        attrs.multiline = is_multiline
        attrs.line_spacing = float(spec.get("line_spacing", 1.0 if is_multiline else 0.0))
        attrs.keep_upright = bool(spec.get("keep_upright", True))
        attrs.bold = bool(spec.get("bold", False))
        attrs.italic = bool(spec.get("italic", False))
        return attrs

    def measure_text_box(
        self,
        text_value: str,
        label_spec: Mapping[str, Any],
        *,
        angle_override: Optional[float] = None,
    ) -> Box:
        text_value = normalize_label_text(text_value)
        text = self.k["Text"]()
        text.value = text_value
        text.position = self.k["Vector2"].from_xy_mm(0.0, 0.0)
        attr_spec = {**label_spec, "text": text_value}
        if angle_override is not None:
            attr_spec["angle_deg"] = angle_override
        attrs = self.make_text_attrs(attr_spec)
        text.attributes = attrs
        return self.box_from_kipy(self.kicad.get_text_extents(text))

    def make_board_text(self, text_value: str, label_spec: Mapping[str, Any], position: Point, layer: Any):
        text_value = normalize_label_text(text_value)
        item = self.k["BoardText"]()
        item.layer = layer
        item.value = text_value
        item.position = self.vec(position)
        item.attributes = self.make_text_attrs({**label_spec, "text": text_value})
        item.locked = bool(label_spec.get("locked", True))
        item.proto.knockout = str(label_spec.get("style", "plain")).lower() == "knockout"
        return item

    def make_rect(
        self,
        box: Box,
        layer: Any,
        stroke_mm: float,
        radius_mm: float,
        *,
        filled: bool = False,
        locked: bool = True,
    ):
        common_types = self.k["common_types"]
        rect = self.k["BoardRectangle"]()
        rect.layer = layer
        rect.top_left = self.vec((box.min_x, box.min_y))
        rect.bottom_right = self.vec((box.max_x, box.max_y))
        rect.attributes.stroke.width = self.k["from_mm"](stroke_mm)
        rect.attributes.stroke.style = common_types.StrokeLineStyle.SLS_SOLID
        rect.attributes.fill.filled = filled
        rect.proto.shape.rectangle.corner_radius.value_nm = self.k["from_mm"](radius_mm)
        rect.locked = locked
        return rect

    def make_graphical_zone(
        self,
        points: Sequence[Point],
        layer: Any,
        name: str,
        *,
        locked: bool = True,
    ):
        zone = self.k["Zone"]()
        zone.type = self.k["ZoneType"].ZT_GRAPHICAL
        zone.layers = [layer]
        zone.name = name
        zone.priority = 0
        zone.min_thickness = self.k["from_mm"](0.25)
        zone.min_island_area = 10 * self.k["from_mm"](1.0) * self.k["from_mm"](1.0)
        zone.island_mode = self.k["IslandRemovalMode"].IRM_ALWAYS
        zone.proto.copper_settings.fill_mode = self.k["ZoneFillMode"].ZFM_SOLID
        zone.border_style = self.k["ZoneBorderStyle"].ZBS_SOLID
        zone.border_hatch_pitch = self.k["from_mm"](0.5)

        outline = self.k["PolygonWithHoles"]()
        for point in points:
            outline.outline.append(self.k["PolyLineNode"].from_point(self.vec(point)))
        outline.outline.closed = True
        zone.outline = outline
        zone.proto.filled = True
        zone.locked = locked
        self.needs_zone_refill = True
        return zone

    def make_pill_zone(
        self,
        box: Box,
        position: Point,
        angle_deg: float,
        layer: Any,
        radius_mm: float,
        *,
        name: str,
        locked: bool = True,
    ):
        points = [(x + position[0], y + position[1]) for x, y in rounded_box_points(box, radius_mm)]
        zone = self.make_graphical_zone(points, layer, name, locked=locked)
        if angle_deg % 360:
            zone.rotate(self.k["Angle"].from_degrees(angle_deg), self.vec(position))
        return zone

    def station_footprint_boxes(self, refs: Sequence[str], footprints: Mapping[str, Any]) -> List[Box]:
        if not refs:
            raise ValueError("station needs refs unless bbox_mm is provided")

        missing = [ref for ref in refs if ref not in footprints]
        if missing:
            raise ValueError(f"footprint ref(s) not found: {', '.join(missing)}")

        boxes = self.board.get_item_bounding_box([footprints[ref] for ref in refs], include_text=False)
        converted = [self.box_from_kipy(box) for box in boxes if box is not None]
        if len(converted) != len(refs):
            raise ValueError("KiCad did not return a bounding box for every station footprint")
        return converted

    def station_core_box(self, refs: Sequence[str], footprints: Mapping[str, Any]) -> Box:
        converted = self.station_footprint_boxes(refs, footprints)
        return Box.union(converted)

    def explicit_box(self, value: Any) -> Optional[Box]:
        if value is None:
            return None
        if not isinstance(value, list) or len(value) != 4:
            raise ValueError("bbox_mm must be [min_x, min_y, max_x, max_y]")
        return Box(float(value[0]), float(value[1]), float(value[2]), float(value[3]))

    def explicit_point(self, value: Any) -> Optional[Point]:
        if value is None:
            return None
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError("position_mm must be [x, y]")
        return (float(value[0]), float(value[1]))

    def text_box_at_position(self, text_value: str, label_spec: Mapping[str, Any], position: Point) -> Box:
        text_box = self.measure_text_box(text_value, label_spec)
        return text_box.move(position[0], position[1])

    def render_station(
        self,
        config: Mapping[str, Any],
        station_id: str,
        station: Mapping[str, Any],
        footprints: Mapping[str, Any],
    ) -> int:
        defaults = config.get("defaults", {})
        layer = self.layer_id(str(station.get("layer", defaults.get("layer", "F.SilkS"))))
        lock_generated = bool(defaults.get("lock_generated_items", True))
        station_class = str(station.get("class", "transfer")).lower()
        refs = _as_list(station.get("refs"))
        snap_mm = float(station.get("snap_mm", defaults.get("snap_mm", 0.25)))

        footprint_boxes: List[Box] = []
        core_box = self.explicit_box(station.get("bbox_mm"))
        if core_box is None:
            footprint_boxes = self.station_footprint_boxes(refs, footprints)
            core_box = Box.union(footprint_boxes)
        else:
            footprint_boxes = [core_box]

        items = []
        content_boxes = [core_box]
        sublabel_items = []
        sublabel_defaults = station.get("sublabel_defaults", {}) or {}
        if not isinstance(sublabel_defaults, Mapping):
            raise ValueError(f"station {station_id!r} sublabel_defaults must be a mapping")
        for index, sublabel in enumerate(_as_mapping_list(station.get("sublabels"))):
            sublabel_spec = deep_merge(
                {
                    "style": "plain",
                    "align": "center",
                    "vertical_align": "center",
                    "size_mm": 1.0,
                    "stroke_mm": 0.15,
                    "angle_deg": 0.0,
                    "locked": lock_generated,
                },
                deep_merge(sublabel_defaults, sublabel),
            )
            text_value = normalize_label_text(str(sublabel_spec.get("text", ""))).strip()
            if not text_value:
                raise ValueError(f"station {station_id!r} sublabel {index} needs text")
            position = self.explicit_point(sublabel_spec.get("position_mm"))
            if position is None:
                raise ValueError(f"station {station_id!r} sublabel {text_value!r} needs position_mm")
            content_boxes.append(self.text_box_at_position(text_value, sublabel_spec, position))
            sublabel_items.append(self.make_board_text(text_value, sublabel_spec, position, layer))

        decoration = station.get("decoration", {}) or {}
        decoration_kind = str(decoration.get("kind", "none")).lower()
        decoration_box = Box.union(content_boxes)

        if decoration_kind == "rounded_rect":
            padding = float(decoration.get("padding_mm", 0.70))
            decoration_box = decoration_box.inflate(padding).snap_outward(snap_mm)
            items.append(
                self.make_rect(
                    decoration_box,
                    layer,
                    float(decoration.get("stroke_mm", 0.10)),
                    float(decoration.get("radius_mm", 0.40)),
                    filled=False,
                    locked=lock_generated,
                )
            )
        elif decoration_kind not in ("none", ""):
            raise ValueError(f"station {station_id!r} has unknown decoration kind {decoration_kind!r}")

        items.extend(sublabel_items)

        label_spec = station.get("label")
        if isinstance(label_spec, Mapping) and label_spec.get("text"):
            text_value = normalize_label_text(str(label_spec["text"]))
            angle_deg = float(label_spec.get("angle_deg", 0.0))
            local_text_box = self.measure_text_box(text_value, label_spec, angle_override=0.0)
            style_name = str(label_spec.get("style", "plain")).lower()
            placement_box = local_text_box
            if style_name == "knockout":
                placement_box = local_text_box.inflate_xy(
                    float(label_spec.get("pill_padding_x_mm", 0.60)),
                    float(label_spec.get("pill_padding_y_mm", 0.35)),
                ).snap_outward(snap_mm)

            if station_class == "standard":
                anchor_radius_mm = float(label_spec.get("anchor_radius_mm", 1.80))
                label_anchor_points = [
                    point
                    for anchor_box in footprint_boxes
                    for point in circle_points(anchor_box.center, anchor_radius_mm)
                ]
            else:
                label_anchor_points = [
                    point
                    for anchor_box in (footprint_boxes if decoration_kind in ("none", "") else [decoration_box])
                    for point in box_points(anchor_box)
                ]
            position = place_shape_against_anchor(
                label_anchor_points,
                rotated_box_points(placement_box, angle_deg),
                str(label_spec.get("side", "E")),
                float(label_spec.get("offset_mm", 1.0)),
                tuple(float(v) for v in label_spec.get("nudge_mm", [0.0, 0.0])),  # type: ignore[arg-type]
                self.explicit_point(label_spec.get("position_mm")),
                str(label_spec["align_x"]) if "align_x" in label_spec else None,
                str(label_spec["align_y"]) if "align_y" in label_spec else None,
            )

            if style_name == "knockout":
                items.append(
                    self.make_pill_zone(
                        placement_box,
                        position,
                        angle_deg,
                        layer,
                        float(label_spec.get("pill_radius_mm", 0.40)),
                        name=f"{station_id}:label",
                        locked=lock_generated,
                    )
                )

            text_item = self.make_board_text(text_value, label_spec, position, layer)
            text_item.locked = lock_generated
            items.append(text_item)

        if not items:
            return 0

        group_name = self.generated_group_name(config, station_id)
        self.delete_generated_group(group_name)
        created = self.board.create_items(items)
        group = self.k["Group"]()
        group.proto.name = group_name
        group.items = created
        self.board.create_items(group)
        return len(created)

    def regenerate(self, config: Mapping[str, Any], station_ids: Optional[Iterable[str]] = None) -> int:
        footprints = self.footprints_by_ref()
        count = 0
        self.needs_zone_refill = False
        for station_id, _class_name, station in station_entries(config, station_ids):
            count += self.render_station(config, station_id, station, footprints)
        if self.needs_zone_refill:
            try:
                self.board.refill_zones(max_poll_seconds=60.0, poll_interval_seconds=1.0)
            except self.k["KipyConnectionError"] as exc:
                print(
                    f"StationGen warning: KiCad is still refilling zones ({exc}). "
                    "Wait for the board to become responsive before saving.",
                    file=sys.stderr,
                )
        return count


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Regenerate StationGen KiCad decoration groups.")
    parser.add_argument("--config", help="station decoration YAML path")
    parser.add_argument("--station", action="append", default=[], help="only regenerate this station id")
    parser.add_argument("--check-config", action="store_true", help="load config without connecting to KiCad")
    parser.add_argument(
        "--capture-selected",
        action="store_true",
        help="write/update one station config entry from the current KiCad PCB selection",
    )
    parser.add_argument("--capture-no-dialog", action="store_true", help="capture using CLI options only")
    parser.add_argument("--station-id", help="station id to create or update when capturing")
    parser.add_argument(
        "--station-class",
        choices=["standard", "transfer", "terminal"],
        help="station class to capture",
    )
    parser.add_argument("--label-text", help="label text to capture")
    parser.add_argument("--label-side", choices=["N", "NE", "E", "SE", "S", "SW", "W", "NW", "C"])
    parser.add_argument("--label-align", choices=["auto", "left", "center", "right"])
    parser.add_argument("--label-align-x", choices=["left", "center", "right"])
    parser.add_argument("--label-align-y", choices=["top", "center", "bottom"])
    parser.add_argument(
        "--exact-label-position",
        action="store_true",
        help="when a selected text item exists, store its current text anchor as position_mm",
    )
    parser.add_argument(
        "--regenerate-after-capture",
        action="store_true",
        help="regenerate the captured station after updating YAML",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.check_config:
        config_path = Path(args.config) if args.config else find_default_config(Path(__file__))
        config = load_config(config_path)
        stations = list(station_entries(config, args.station))
        print(f"loaded {config_path} ({len(stations)} station(s))")
        return 0

    generator = StationGenIPC()
    if args.config:
        config_path = Path(args.config)
    else:
        project_path = Path(generator.board.get_project().path)
        config_path = find_default_config(project_path)

    if args.capture_selected:
        result = generator.capture_selected_station(config_path, args)
        if result:
            print(result)
        else:
            print("capture cancelled")
        return 0

    config = load_config(config_path)
    count = generator.regenerate(config, args.station)
    print(f"generated {count} StationGen item(s) from {config_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"StationGen error: {exc}", file=sys.stderr)
        raise SystemExit(1)
