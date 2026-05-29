from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Sequence, Tuple

from stationgen.config import load_yaml


class FlowList(list):
    """YAML list that should be written on one line."""


class LiteralString(str):
    """YAML string that should be written in block literal style."""


FLOW_LIST_KEYS = {"refs", "position_mm", "nudge_mm"}


def normalize_label_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def station_id_from_label(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", value.strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized


def natural_ref_key(value: str) -> Tuple[Any, ...]:
    pieces = re.split(r"(\d+)", value)
    return tuple(int(piece) if piece.isdigit() else piece.lower() for piece in pieces)


def sorted_refs(refs: Iterable[str]) -> list[str]:
    return sorted({str(ref) for ref in refs}, key=natural_ref_key)


def compact_float(value: float, digits: int = 5) -> float:
    return round(float(value), digits)


def build_station_entry(
    *,
    station_class: str,
    refs: Sequence[str],
    label_text: str,
    side: str | None = None,
    align: str | None = None,
    vertical_align: str | None = None,
    align_x: str | None = None,
    align_y: str | None = None,
    cross_align: str | None = None,
    angle_deg: float | None = None,
    position_mm: Sequence[float] | None = None,
) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "class": station_class,
        "refs": sorted_refs(refs),
    }

    normalized_label_text = normalize_label_text(label_text).strip()
    if normalized_label_text:
        label: Dict[str, Any] = {"text": normalized_label_text}
        if side:
            label["side"] = side
        if align:
            label["align"] = align
        if vertical_align:
            label["vertical_align"] = vertical_align
        if align_x:
            label["align_x"] = align_x
        if align_y:
            label["align_y"] = align_y
        if cross_align:
            label["cross_align"] = cross_align
        if position_mm is not None:
            label["position_mm"] = [compact_float(position_mm[0]), compact_float(position_mm[1])]
        if angle_deg is not None and abs(float(angle_deg)) > 0.0001:
            label["angle_deg"] = compact_float(float(angle_deg), digits=3)
        entry["label"] = label

    return entry


def prepare_for_yaml_dump(value: Any, key: str | None = None) -> Any:
    if isinstance(value, Mapping):
        return {
            item_key: prepare_for_yaml_dump(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        items = [prepare_for_yaml_dump(item) for item in value]
        if key in FLOW_LIST_KEYS:
            return FlowList(items)
        return items
    if isinstance(value, str) and "\n" in value:
        return LiteralString(value)
    return value


def write_station_config(path: Path, data: Mapping[str, Any]) -> None:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("StationGen config editing requires PyYAML.") from exc

    class StationGenDumper(yaml.SafeDumper):
        pass

    def represent_flow_list(dumper: yaml.Dumper, sequence: FlowList):
        return dumper.represent_sequence("tag:yaml.org,2002:seq", sequence, flow_style=True)

    def represent_literal_string(dumper: yaml.Dumper, value: LiteralString):
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")

    StationGenDumper.add_representer(FlowList, represent_flow_list)
    StationGenDumper.add_representer(LiteralString, represent_literal_string)
    rendered = yaml.dump(
        prepare_for_yaml_dump(data),
        Dumper=StationGenDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=False,
    )
    path.write_text(rendered, encoding="utf-8")


def upsert_station_config(path: Path, station_id: str, station: Mapping[str, Any]) -> str:
    data = load_yaml(path) if path.exists() else {}
    stations = data.setdefault("stations", {})
    if not isinstance(stations, MutableMapping):
        raise ValueError("config key 'stations' must be a mapping")

    action = "updated" if station_id in stations else "created"
    stations[station_id] = dict(station)
    write_station_config(path, data)
    return action
