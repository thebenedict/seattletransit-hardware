# Seattle Transit Live

Schematic and PCB for a circuit board map of Seattle area public transit.

This board uses an `ESP32-C3` to drive `247` individually addressable `WS2812` LEDs arranged as a stylized regional transit map. The LEDs are split across four chains and are intended to represent live movement across rail, ferry, water taxi, and monorail routes.

## Main Functionality

- Displays a physical transit map with addressable LEDs placed along the network.
- Uses an `ESP32-C3-MINI-1` as the main controller.
- Takes power and USB data over `USB-C`.
- Drives the LED chains through level shifting and a switchable LED power rail.
- Includes an ambient light sensor for auto-dimming in software.

## Repo Contents

- `transit.kicad_sch`, `transit.kicad_pcb`, and the sub-sheet `.kicad_sch` files contain the Rev A schematic and PCB layout.
- `production/` contains fabrication outputs, BOM data, positions, and packaged manufacturing files.
- `tools/` contains scripts and transit GIS/GTFS inputs used to generate LED map configuration data.
- `3d_views/` and `web/public/` contain renders and SVG exports of the board.


## More soon

- Board firmware and supporting cloud service to be published separately.
