# LED Map Generation Pipeline

Generates `rev-a.json` (the board config) from KiCad source files and GTFS data.

## Required Input Files

### From KiCad project (you provide):
```
kicad/
├── transit.kicad_sch          # Main schematic (for reference)
├── rail_w.kicad_sch           # Rail West chain (106 LEDs, part 1)
├── rail_w_southbound.kicad_sch # Rail West chain (part 2, continues from rail_w)
├── rail_e.kicad_sch           # Rail East chain (48 LEDs)
├── ferries_n.kicad_sch        # Ferries North chain (46 LEDs)
├── ferries_s.kicad_sch        # Ferries South chain (47 LEDs)
└── transit.kicad_pcb          # PCB layout (for LED physical positions)
```

### From GTFS / transit agencies (downloaded):
- Sound Transit GTFS `stops.txt` and `shapes.txt`
- WSF terminal coordinates
- KC Water Taxi stop coordinates

### Hand-maintained:
- `station_labels.csv` — maps LED reference designators to station names and GTFS stop IDs

## Pipeline Steps

### Step 1: `01_extract_chain_order.py`
**Input:** KiCad `.kicad_sch` files (sub-sheets)
**Output:** `chain_order.json`

Parses schematic wiring to determine LED daisy-chain order per chain.
Traces DO→DI pin connections through wires.

```bash
python 01_extract_chain_order.py /path/to/kicad/
```

### Step 2: `02_extract_pcb_positions.py` (TODO)
**Input:** `transit.kicad_pcb`
**Output:** `pcb_positions.json`

Extracts board-space (x, y) position of every LED footprint from the PCB layout.

### Step 3: `03_merge_leds.py` (TODO)
**Input:** `chain_order.json` + `pcb_positions.json`
**Output:** `leds_merged.json`

Joins chain order with PCB positions. Assigns global indices. Validates 247 total.

### Step 4: `04_assign_stations.py` (TODO)
**Input:** `leds_merged.json` + `station_labels.csv` + GTFS data
**Output:** `leds_with_geo.json`

Enriches LEDs with station labels and geographic coordinates (lat/lng).

### Step 5: `05_build_routes.py` (TODO)
**Input:** `leds_with_geo.json` + GTFS shapes + route definitions
**Output:** `routes.json`

Constructs route objects with geoPath, ledIndices, ledProgress,
and progressThresholds. Handles shared LEDs across routes.

### Step 6: `06_assemble_rev_a.py` (TODO)
**Input:** `leds_with_geo.json` + `routes.json`
**Output:** `rev-a.json`

Assembles the final board config JSON with all sections.

### Step 7: `07_validate.py` (TODO)
**Input:** `rev-a.json`
**Output:** Validation report + visual overlay PNG

Comprehensive validation suite.

## Shared LED Handling

Three LEDs are shared across routes:

1. **Seattle water taxi terminal** — shared by Seattle–West Seattle WT and Seattle–Vashon WT
2. **First waypoint after Seattle terminal** — same two routes share their first leg
3. **Vashon triangle crossing** — Seattle–Vashon WT crosses Fauntleroy–Southworth WSF

These appear in multiple routes' `ledIndices` arrays but only once in the `leds` array.
Collision policy: last-writer-wins (active vessel takes priority).