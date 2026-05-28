from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

import pcbnew


REPO_ROOT = Path("/Users/michael/Documents/transit")
CONFIG_PATH = REPO_ROOT / "scripts" / "station_decorations.yaml"
STATIONGEN_PYTHON = Path(
    "/Users/michael/Library/Caches/KiCad/10.0/python-environments/"
    "com.charlesstreetlabs.stationgen/bin/python"
)


def _show_message(title: str, message: str) -> None:
    try:
        import wx

        wx.MessageBox(message, title, wx.OK | wx.ICON_INFORMATION)
    except Exception:
        print(f"{title}: {message}")


def _notify(title: str, message: str, *, refresh: bool = False) -> None:
    try:
        import wx

        if refresh:
            wx.CallAfter(pcbnew.Refresh)
        wx.CallAfter(_show_message, title, message)
    except Exception:
        if refresh:
            try:
                pcbnew.Refresh()
            except Exception:
                pass
        _show_message(title, message)


def _run_stationgen(extra_args: list[str]) -> None:
    if not STATIONGEN_PYTHON.exists():
        _show_message(
            "StationGen",
            "StationGen's KiCad Python environment does not exist yet. "
            "Refresh IPC plugins or run StationGen once from the terminal.",
        )
        return

    command = [
        str(STATIONGEN_PYTHON),
        "-m",
        "stationgen",
        "--config",
        str(CONFIG_PATH),
        *extra_args,
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{REPO_ROOT}{os.pathsep}{env.get('PYTHONPATH', '')}"

    def worker() -> None:
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
        )

        output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
        if result.returncode:
            _notify("StationGen failed", output or f"Command exited with {result.returncode}.")
            return
        _notify("StationGen", output or "Done.", refresh=True)

    threading.Thread(target=worker, daemon=True).start()


class RegenerateStationDecorations(pcbnew.ActionPlugin):
    def defaults(self) -> None:
        self.name = "Regenerate Station Decorations"
        self.category = "StationGen"
        self.description = "Regenerate StationGen silkscreen decoration groups from YAML."
        self.show_toolbar_button = False

    def Run(self) -> None:
        _run_stationgen([])


class CaptureSelectedStationToConfig(pcbnew.ActionPlugin):
    def defaults(self) -> None:
        self.name = "Capture Selected Station to Config"
        self.category = "StationGen"
        self.description = "Create or update a StationGen YAML entry from selected footprints."
        self.show_toolbar_button = False

    def Run(self) -> None:
        _run_stationgen(["--capture-selected"])


RegenerateStationDecorations().register()
CaptureSelectedStationToConfig().register()
