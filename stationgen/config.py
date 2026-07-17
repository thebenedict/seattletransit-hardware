from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional


DEFAULTS: Dict[str, Any] = {
    "defaults": {
        "layer": "F.SilkS",
        "snap_mm": 0.25,
        "generated_group_prefix": "stationgen:",
        "lock_generated_items": True,
    },
    "styles": {
        "standard": {
            "decoration": {
                "kind": "none",
            },
            "label": {
                "style": "plain",
                "align": "auto",
                "vertical_align": "center",
                "side": "E",
                "offset_mm": 1.00,
                "anchor_radius_mm": 1.80,
                "size_mm": 1.20,
                "stroke_mm": 0.15,
                "angle_deg": 0.0,
            },
        },
        "ferry_port": {
            "decoration": {
                "kind": "none",
            },
            "label": {
                "style": "plain",
                "align": "auto",
                "vertical_align": "center",
                "side": "E",
                "offset_mm": 0.80,
                "anchor_radius_mm": 2.70,
                "size_mm": 1.20,
                "stroke_mm": 0.15,
                "angle_deg": 0.0,
            },
        },
        "transfer": {
            "decoration": {
                "kind": "rounded_rect",
                "padding_mm": 0.70,
                "content_radius_mm": 1.30,
                "stroke_mm": 0.10,
                "radius_mm": 0.40,
                "angle_deg": 0.0,
            },
            "label": {
                "style": "plain",
                "align": "auto",
                "vertical_align": "center",
                "side": "E",
                "offset_mm": 1.00,
                "size_mm": 1.20,
                "stroke_mm": 0.15,
                "angle_deg": 0.0,
            },
        },
        "terminal": {
            "decoration": {
                "kind": "none",
            },
            "label": {
                "style": "knockout",
                "align": "center",
                "vertical_align": "center",
                "side": "S",
                "offset_mm": 1.00,
                "size_mm": 1.20,
                "stroke_mm": 0.15,
                "angle_deg": 0.0,
                "pill_padding_x_mm": 0.60,
                "pill_padding_y_mm": 0.35,
                "pill_radius_mm": 0.40,
                "pill_stroke_mm": 0.10,
            },
        },
    },
    "stations": {},
}


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> Dict[str, Any]:
    out = deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), MutableMapping):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = deepcopy(value)
    return out


def load_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "StationGen YAML support requires PyYAML. Install the plugin requirements "
            "or run `python -m pip install PyYAML`."
        ) from exc

    with path.open("r", encoding="utf-8") as config_file:
        data = yaml.safe_load(config_file) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping at the top level")
    return data


def load_config(path: Path) -> Dict[str, Any]:
    return deep_merge(DEFAULTS, load_yaml(path))


def find_default_config(start: Optional[Path] = None) -> Path:
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent

    for parent in [current, *current.parents]:
        candidate = parent / "scripts" / "station_decorations.yaml"
        if candidate.exists():
            return candidate
        if (parent / "transit.kicad_pro").exists():
            return candidate

    return Path.cwd() / "scripts" / "station_decorations.yaml"


def station_entries(config: Mapping[str, Any], station_ids: Optional[Iterable[str]] = None):
    stations = config.get("stations", {})
    if not isinstance(stations, Mapping):
        raise ValueError("config key 'stations' must be a mapping")

    selected = list(station_ids or [])
    if selected:
        missing = [station_id for station_id in selected if station_id not in stations]
        if missing:
            raise ValueError(f"station(s) not found in config: {', '.join(missing)}")
        station_ids_iter = selected
    else:
        station_ids_iter = list(stations.keys())

    styles = config.get("styles", {})
    if not isinstance(styles, Mapping):
        raise ValueError("config key 'styles' must be a mapping")

    for station_id in station_ids_iter:
        station = stations[station_id] or {}
        if not isinstance(station, Mapping):
            raise ValueError(f"station {station_id!r} must be a mapping")
        if station.get("enabled", True) is False:
            continue

        class_name = str(station.get("class", "transfer"))
        style = styles.get(class_name)
        if not isinstance(style, Mapping):
            raise ValueError(f"station {station_id!r} references unknown class {class_name!r}")
        yield station_id, class_name, deep_merge(style, station)
