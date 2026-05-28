import tempfile
import unittest
from pathlib import Path

from stationgen.capture import (
    build_station_entry,
    normalize_label_text,
    sorted_refs,
    station_id_from_label,
    upsert_station_config,
)


class CaptureConfigTests(unittest.TestCase):
    def test_station_id_from_label(self):
        self.assertEqual(station_id_from_label("Intl. District / Chinatown"), "intl_district_chinatown")
        self.assertEqual(station_id_from_label("  Tacoma Dome  "), "tacoma_dome")

    def test_refs_are_naturally_sorted_and_deduped(self):
        self.assertEqual(sorted_refs(["LED10", "LED2", "LED2", "LED1"]), ["LED1", "LED2", "LED10"])

    def test_normalize_label_text_converts_kicad_carriage_returns(self):
        self.assertEqual(normalize_label_text("Intl. District\rChinatown"), "Intl. District\nChinatown")
        self.assertEqual(normalize_label_text("A\r\nB"), "A\nB")

    def test_build_station_entry_preserves_normalized_multiline_label(self):
        entry = build_station_entry(
            station_class="standard",
            refs=["LED2", "LED1"],
            label_text="Intl. District\rChinatown",
            side="E",
            align="left",
        )

        self.assertEqual(entry["class"], "standard")
        self.assertEqual(entry["label"]["text"], "Intl. District\nChinatown")

    def test_build_station_entry_omits_empty_optionals(self):
        entry = build_station_entry(
            station_class="transfer",
            refs=["LED2", "LED1"],
            label_text="Example",
            side="N",
            align="left",
            align_x="left",
        )

        self.assertEqual(
            entry,
            {
                "class": "transfer",
                "refs": ["LED1", "LED2"],
                "label": {
                    "text": "Example",
                    "side": "N",
                    "align": "left",
                    "align_x": "left",
                },
            },
        )

    def test_upsert_station_config_preserves_top_level_shape(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML is not installed in this Python environment")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "station_decorations.yaml"
            path.write_text("defaults:\n  layer: F.SilkS\nstations: {}\n", encoding="utf-8")

            action = upsert_station_config(
                path,
                "example",
                {
                    "class": "transfer",
                    "refs": ["LED1", "LED2"],
                    "label": {"text": "Example", "position_mm": [1.0, 2.0]},
                },
            )

            self.assertEqual(action, "created")
            rendered = path.read_text(encoding="utf-8")
            self.assertIn("defaults:", rendered)
            self.assertIn("stations:", rendered)
            self.assertIn("refs: [LED1, LED2]", rendered)
            self.assertIn("position_mm: [1.0, 2.0]", rendered)

    def test_upsert_station_config_writes_multiline_text_as_literal_block(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML is not installed in this Python environment")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "station_decorations.yaml"
            path.write_text("stations: {}\n", encoding="utf-8")

            upsert_station_config(
                path,
                "intl_district_chinatown",
                {
                    "class": "standard",
                    "refs": ["LED227", "LED627"],
                    "label": {"text": "Intl. District\nChinatown"},
                },
            )

            rendered = path.read_text(encoding="utf-8")
            self.assertIn("text: |-", rendered)
            self.assertIn("  Intl. District", rendered)
            self.assertIn("  Chinatown", rendered)


if __name__ == "__main__":
    unittest.main()
