# StationGen

StationGen generates disposable silkscreen decoration for named stop groups.
LED footprints remain owned by the board and existing placement tools; StationGen
only owns generated graphics in groups named `stationgen:<station_id>`.

## Current Scope

- Transfer stations: rounded rectangle generated from the union of LED footprint
  bounding boxes, expanded by a style padding token. Rectangles may also use
  `decoration.angle_deg` for angled transfer groups.
- Standard stations: plain generated label text, with no station decoration.
- Ferry ports: plain generated label text, with no station decoration, using a
  larger label anchor radius for the WS2812B Mini footprint.
- Terminal stations: filled graphical-zone label pill plus knockout text. The
  zone fill is what lets KiCad's knockout text cut through the silkscreen fill.
- Labels: side-based placement around the station decoration/core with explicit
  `angle_deg`, `offset_mm`, alignment, and optional `position_mm` / `nudge_mm`
  exceptions.

No ferry-only terminal decoration is implemented yet.

## Config

The project config is [`scripts/station_decorations.yaml`](../scripts/station_decorations.yaml).
Styles define shared tokens; stations define intent:

```yaml
stations:
  intl_district_chinatown:
    class: standard
    refs: [LED1727, LED1827]
    label:
      text: |-
        Intl. District
        Chinatown
      align: left
      side: E

  mukilteo:
    class: transfer
    refs: [LED1101, LED1409, LED1421]
    label:
      text: Mukilteo
      align: auto
      side: NW
      offset_mm: 0.80

  vashon_island:
    class: ferry_port
    refs: [LED401]
    label:
      text: Vashon Island
      side: SW
      align: right
      align_x: right
      angle_deg: 45.0

  westlake:
    class: transfer
    refs: [LED1601, LED1733, LED1821]
    decoration:
      angle_deg: 315.0
```

Label positions use compass sides: `N`, `NE`, `E`, `SE`, `S`, `SW`, `W`,
`NW`, or `C`. `offset_mm` is projected clearance from the station geometry to
the rendered label geometry in that compass direction, so angled labels and
horizontal labels use the same spacing rule. Standard labels anchor against a
style-controlled halo around each selected LED center, which avoids rotated
footprint bounding boxes creating larger apparent gaps. Tune
`anchor_radius_mm` on the standard label style if the global standard-station
spacing needs to move in or out. Transfer labels anchor against the generated
rounded rectangle. Plain labels default to `align: auto`, which left-aligns
east-side labels, right-aligns west-side labels, and centers north/south labels
so text grows away from the station. Knockout pill labels default to centered
text.
Use YAML literal blocks for multiline labels; StationGen also normalizes KiCad
captured carriage returns to normal newline characters. Multiline generated text
defaults to KiCad `line_spacing: 1.0`; override `label.line_spacing` only when a
specific label needs tighter or looser leading.
Use `align_x: left|center|right` or `align_y: top|center|bottom` when a label
should keep a side placement but align one measured label edge with the station
decoration edge. For example, `side: N` plus `align_x: left` puts the label above
the station while aligning its left edge to the rectangle's left edge.
Use `cross_align: top|bottom|left|right|min|center|max` when the label should
stay on the requested side, but its centerline should align with a different
part of the station anchor along the cross axis. For example, `side: E` plus
`cross_align: top` keeps the label east of a diagonal LED pair while lining it
up with the upper LED instead of centering it between both LEDs.
For angled transfer boxes, set `decoration.angle_deg`; StationGen measures the
station content in that rotated coordinate system, applies the normal
`padding_mm`, and then generates a polygonal rounded rectangle on silkscreen.
Angled boxes use `decoration.content_radius_mm` as the visual radius around each
selected LED center before padding, which keeps diagonal groups from inheriting
oversized screen-aligned footprint bounding boxes. Labels for transfer stations
anchor against the generated rotated outline.

Stations can define internal `sublabels` for special cases like Seattle's
`Pier 50` and `Pier 52` labels. Sublabel text boxes are included in the
station's rounded-rectangle content box before normal decoration padding is
applied, and the sublabel text is generated inside the station group:

```yaml
stations:
  seattle:
    class: transfer
    refs: [LED701, LED514]
    sublabel_defaults:
      align: center
      vertical_align: center
      size_mm: 1.00
      stroke_mm: 0.15
    sublabels:
      - text: Pier 52
        position_mm: [125.77, 52.80]
      - text: Pier 50
        position_mm: [125.77, 60.80]
```

## KiCad Plugin

The repo-local plugin lives in [`stationgen`](../stationgen). This is an IPC API
plugin, not a legacy `pcbnew` action plugin, so it belongs in KiCad's versioned
`plugins` directory, not `scripting/plugins`:

```sh
mkdir -p /Users/michael/Documents/KiCad/10.0/plugins
rm -f /Users/michael/Documents/KiCad/10.0/plugins/stationgen
ln -s /Users/michael/Documents/transit/stationgen \
  /Users/michael/Documents/KiCad/10.0/plugins/stationgen
```

KiCad will create the plugin Python environment and install
[`stationgen/requirements.txt`](../stationgen/requirements.txt). KiCad's
visible PCB Editor menu still scans the legacy scripting plugin folder, so this
project also installs a small legacy Action Plugin launcher at:

```sh
/Users/michael/Documents/KiCad/10.0/scripting/plugins/stationgen_legacy_action_plugin.py
```

That launcher delegates to the IPC implementation. Use the PCB editor actions
under `Tools -> External Plugins`:

- `Regenerate Station Decorations`: delete and recreate generated decoration
  groups from the YAML config.
- `Capture Selected Station to Config`: create or update one station entry from
  the current selection.

On this Mac, these menu actions are also assigned as KiCad app shortcuts:

- `Cmd+Option+R`: `Regenerate Station Decorations`
- `Cmd+Option+C`: `Capture Selected Station to Config`

Make sure KiCad's IPC API is enabled first: KiCad -> Settings... -> Plugins ->
Enable KiCad API. Restart KiCad after adding the symlink. The action should then
appear in the PCB Editor as `Regenerate Station Decorations`; the first load may
take a little while while KiCad builds the plugin Python environment. If the
menu only shows `Refresh Plugins` and `Reveal Plugin Folder in Finder`, click
`Refresh Plugins`. That menu's reveal command opens
`/Users/michael/Documents/KiCad/10.0/scripting/plugins`, not the IPC plugin
folder.

If KiCad creates the plugin environment but does not show the action in the UI,
leave the board open and run StationGen directly through the KiCad-created
environment:

```sh
cd /Users/michael/Documents/transit
/Users/michael/Library/Caches/KiCad/10.0/python-environments/com.charlesstreetlabs.stationgen/bin/python \
  -m stationgen --config scripts/station_decorations.yaml
```

To add another station without typing LED reference designators, select the
station's LED footprints in the PCB editor. Optionally select one existing free
text label too; StationGen will use it as the default label text, label angle,
and side hint. Plain selected text defaults to the `standard` class; knockout
selected text defaults to `terminal`; selecting only footprints defaults to
`transfer`. Then run `Capture Selected Station to Config`. The dialog lets you
choose the station id, `standard`, `ferry_port`, `transfer`, or `terminal`, side
placement, text alignment, optional edge/cross alignment, and whether to
regenerate that station immediately. After a successful capture, StationGen
removes the selected source text label from the board so the generated label is
the only copy.

The same capture workflow can run from the terminal while the board is open:

```sh
cd /Users/michael/Documents/transit
/Users/michael/Library/Caches/KiCad/10.0/python-environments/com.charlesstreetlabs.stationgen/bin/python \
  -m stationgen \
  --config scripts/station_decorations.yaml \
  --capture-selected
```

For a non-dialog capture, pass the needed options explicitly:

```sh
/Users/michael/Library/Caches/KiCad/10.0/python-environments/com.charlesstreetlabs.stationgen/bin/python \
  -m stationgen \
  --config scripts/station_decorations.yaml \
  --capture-selected \
  --capture-no-dialog \
  --station-id northgate \
  --station-class transfer \
  --label-text Northgate \
  --label-side E \
  --label-align auto \
  --label-cross-align top \
  --regenerate-after-capture
```

Terminal knockout labels trigger a KiCad zone refill. If KiCad remains busy
longer than the IPC wait loop, StationGen may print a warning but the refill can
still finish in KiCad; wait for the board to become responsive before saving.

Do not create a `stationgen` symlink inside the repo's own
`/Users/michael/Documents/transit/stationgen` directory. KiCad 10.0.1 traverses
plugin folders recursively during startup, and a recursive symlink can crash
KiCad during plugin discovery.

StationGen only deletes and recreates groups with the configured generated
prefix. Existing hand-drawn silkscreen labels and rectangles are left alone, so
delete or move those manually as stations are migrated into the YAML config.

For command-line config validation:

```sh
python -m pip install -r stationgen/requirements.txt
python -m stationgen --config scripts/station_decorations.yaml --check-config
```
