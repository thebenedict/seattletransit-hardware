#!/usr/bin/env python3
"""
Step 1: Extract LED daisy-chain wiring order from KiCad schematics.

Parses KiCad v9 .kicad_sch files (S-expression format) to determine the
wiring order of WS2812B LEDs on each chain. Traces DO→DI net connections
through wires to reconstruct the physical chain order.

Input:  Directory containing .kicad_sch files (main + sub-sheets)
Output: chain_order.json

Usage:
    python 01_extract_chain_order.py /path/to/kicad/project
    python 01_extract_chain_order.py /path/to/kicad/project --output chain_order.json
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# S-expression parser (minimal, sufficient for KiCad schematic files)
# ---------------------------------------------------------------------------

def parse_sexpr(text: str) -> list:
    """Parse an S-expression string into nested Python lists and strings."""
    tokens = _tokenize(text)
    result, _ = _parse_tokens(tokens, 0)
    return result


def _tokenize(text: str) -> list[str]:
    """Tokenize S-expression text into a flat list of tokens."""
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in ' \t\n\r':
            i += 1
        elif c == '(':
            tokens.append('(')
            i += 1
        elif c == ')':
            tokens.append(')')
            i += 1
        elif c == '"':
            # Quoted string
            j = i + 1
            while j < n:
                if text[j] == '\\':
                    j += 2
                elif text[j] == '"':
                    break
                else:
                    j += 1
            tokens.append(text[i+1:j])  # strip quotes
            i = j + 1
        else:
            # Unquoted atom
            j = i
            while j < n and text[j] not in ' \t\n\r()':
                j += 1
            tokens.append(text[i:j])
            i = j
    return tokens


def _parse_tokens(tokens: list[str], pos: int) -> tuple[list, int]:
    """Recursively parse tokens starting at pos. Returns (parsed, new_pos)."""
    if tokens[pos] != '(':
        return tokens[pos], pos + 1

    pos += 1  # skip '('
    items = []
    while pos < len(tokens) and tokens[pos] != ')':
        item, pos = _parse_tokens(tokens, pos)
        items.append(item)
    pos += 1  # skip ')'
    return items, pos


# ---------------------------------------------------------------------------
# S-expression query helpers
# ---------------------------------------------------------------------------

def sexpr_tag(node) -> Optional[str]:
    """Get the tag (first element) of an S-expression list."""
    if isinstance(node, list) and len(node) > 0 and isinstance(node[0], str):
        return node[0]
    return None


def sexpr_find(node: list, tag: str) -> list:
    """Find all direct children with the given tag."""
    return [child for child in node if sexpr_tag(child) == tag]


def sexpr_find_one(node: list, tag: str):
    """Find first direct child with the given tag, or None."""
    for child in node:
        if sexpr_tag(child) == tag:
            return child
    return None


def sexpr_val(node: list, tag: str, default=None):
    """Get the value (second element) of a tagged child."""
    child = sexpr_find_one(node, tag)
    if child and len(child) > 1:
        return child[1]
    return default


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PinDef:
    """Pin definition from a library symbol."""
    number: str
    name: str
    x: float  # relative to symbol origin
    y: float


@dataclass
class LedSymbol:
    """An LED symbol instance in the schematic."""
    ref: str              # e.g. "LED411"
    lib_id: str           # e.g. "EasyEDA:WS2812B-2020"
    package: str          # "2020" or "mini"
    uuid: str
    x: float              # symbol placement position
    y: float
    mirror_y: bool
    rotation: float       # degrees
    # Absolute pin positions (computed)
    di_pin: tuple[float, float] = (0.0, 0.0)
    do_pin: tuple[float, float] = (0.0, 0.0)


@dataclass
class Wire:
    """A wire segment in the schematic."""
    x1: float
    y1: float
    x2: float
    y2: float


# ---------------------------------------------------------------------------
# Pin position definitions from library symbols
# ---------------------------------------------------------------------------

# WS2812B-2020 pin positions (relative to symbol origin, before transform):
#   Pin 1 (DO):  at (0, -2.54)
#   Pin 2 (GND): at (0, -5.08)
#   Pin 3 (DI):  at (17.78, -5.08)
#   Pin 4 (VDD): at (17.78, -2.54)
WS2812B_2020_DO = PinDef("1", "DO", 0.0, -2.54)
WS2812B_2020_DI = PinDef("3", "DI", 17.78, -5.08)

# WS2812B-MINI pin positions:
#   Pin 1 (VDD):  at (0, -2.54)
#   Pin 2 (DOUT): at (0, -5.08)
#   Pin 3 (VSS):  at (20.32, -5.08)
#   Pin 4 (DIN):  at (20.32, -2.54)
WS2812B_MINI_DO = PinDef("2", "DOUT", 0.0, -5.08)
WS2812B_MINI_DI = PinDef("4", "DIN", 20.32, -2.54)

# Map lib_id patterns to pin definitions
PIN_DEFS = {
    "WS2812B-2020": (WS2812B_2020_DI, WS2812B_2020_DO),
    "WS2812B-MINI": (WS2812B_MINI_DI, WS2812B_MINI_DO),
}


def transform_pin(sym_x: float, sym_y: float, pin_x: float, pin_y: float,
                   mirror_y: bool, rotation: float) -> tuple[float, float]:
    """
    Transform a pin's relative position to absolute schematic coordinates.

    KiCad applies transforms in this order:
    1. Mirror Y (negate X)
    2. Rotate
    3. Translate to symbol position
    """
    px, py = pin_x, pin_y

    if mirror_y:
        px = -px

    # Apply rotation (KiCad uses degrees, CCW positive)
    if rotation != 0:
        import math
        rad = math.radians(rotation)
        cos_r = math.cos(rad)
        sin_r = math.sin(rad)
        px, py = px * cos_r - py * sin_r, px * sin_r + py * cos_r

    # Translate to symbol position
    abs_x = round(sym_x + px, 4)
    abs_y = round(sym_y + py, 4)
    return abs_x, abs_y


# ---------------------------------------------------------------------------
# Schematic parsing
# ---------------------------------------------------------------------------

def parse_schematic(filepath: Path) -> tuple[list[LedSymbol], list[Wire], Optional[str]]:
    """
    Parse a single .kicad_sch file. Returns (leds, wires, global_label).

    global_label is the chain input label (e.g. "Ferries N") if found.
    """
    text = filepath.read_text(encoding='utf-8')
    root = parse_sexpr(text)

    leds = []
    wires = []
    global_label = None

    for node in root:
        tag = sexpr_tag(node)

        if tag == 'symbol':
            led = _parse_led_symbol(node)
            if led:
                leds.append(led)

        elif tag == 'wire':
            wire = _parse_wire(node)
            if wire:
                wires.append(wire)

        elif tag == 'global_label':
            # The chain input label — the data signal entering this sub-sheet
            if len(node) > 1 and isinstance(node[1], str):
                label_text = node[1]
                # Only care about chain-related labels
                if any(name in label_text for name in ['Rail', 'Ferries']):
                    global_label = label_text

    return leds, wires, global_label


def _parse_led_symbol(node: list) -> Optional[LedSymbol]:
    """Parse a symbol node, returning LedSymbol if it's a WS2812B LED."""
    lib_id = sexpr_val(node, 'lib_id')
    if not lib_id or not isinstance(lib_id, str):
        return None

    # Identify LED type
    package = None
    if 'WS2812B-2020' in lib_id:
        package = '2020'
    elif 'WS2812B-MINI' in lib_id or 'WS2812B-Mini' in lib_id:
        package = 'mini'
    else:
        return None

    # Get reference designator from instances
    ref = None
    instances = sexpr_find_one(node, 'instances')
    if instances:
        for project in sexpr_find(instances, 'project'):
            for path in sexpr_find(project, 'path'):
                r = sexpr_val(path, 'reference')
                if r and isinstance(r, str) and r.startswith('LED'):
                    ref = r
                    break

    # Fallback: get from Reference property
    if not ref:
        for prop in sexpr_find(node, 'property'):
            if len(prop) > 2 and prop[1] == 'Reference':
                val = prop[2]
                if isinstance(val, str) and val.startswith('LED'):
                    ref = val
                    break

    if not ref:
        return None

    # Get position
    at_node = sexpr_find_one(node, 'at')
    if not at_node or len(at_node) < 3:
        return None

    try:
        sym_x = float(at_node[1])
        sym_y = float(at_node[2])
        rotation = float(at_node[3]) if len(at_node) > 3 else 0.0
    except (ValueError, IndexError):
        return None

    # Check for mirror
    mirror_y = False
    for item in node:
        if isinstance(item, list) and sexpr_tag(item) == 'mirror' and len(item) > 1:
            mirror_y = item[1] == 'y'
        elif item == 'mirror':
            # Sometimes mirror is followed by 'y' as next sibling
            pass
    # Also check for bare 'mirror' with 'y' — KiCad sometimes uses:
    # (mirror y) as a standalone child
    for i, item in enumerate(node):
        if isinstance(item, list) and len(item) == 2 and item[0] == 'mirror':
            mirror_y = item[1] == 'y'

    uuid = ''
    uuid_node = sexpr_find_one(node, 'uuid')
    if uuid_node and len(uuid_node) > 1:
        uuid = uuid_node[1]

    # Determine pin definitions
    key = 'WS2812B-MINI' if package == 'mini' else 'WS2812B-2020'
    di_def, do_def = PIN_DEFS[key]

    # Compute absolute pin positions
    di_pos = transform_pin(sym_x, sym_y, di_def.x, di_def.y, mirror_y, rotation)
    do_pos = transform_pin(sym_x, sym_y, do_def.x, do_def.y, mirror_y, rotation)

    led = LedSymbol(
        ref=ref,
        lib_id=lib_id,
        package=package,
        uuid=uuid,
        x=sym_x,
        y=sym_y,
        mirror_y=mirror_y,
        rotation=rotation,
        di_pin=di_pos,
        do_pin=do_pos,
    )
    return led


def _parse_wire(node: list) -> Optional[Wire]:
    """Parse a wire node."""
    pts_node = sexpr_find_one(node, 'pts')
    if not pts_node:
        return None

    xy_nodes = sexpr_find(pts_node, 'xy')
    if len(xy_nodes) < 2:
        return None

    try:
        x1 = float(xy_nodes[0][1])
        y1 = float(xy_nodes[0][2])
        x2 = float(xy_nodes[1][1])
        y2 = float(xy_nodes[1][2])
    except (ValueError, IndexError):
        return None

    return Wire(x1, y1, x2, y2)


# ---------------------------------------------------------------------------
# Net tracing — Union-Find for connecting wire endpoints and pin positions
# ---------------------------------------------------------------------------

TOLERANCE = 0.01  # mm — KiCad coordinates are in mm with high precision


def snap_coord(val: float) -> float:
    """Snap coordinate to grid to handle floating point comparison."""
    return round(val, 2)


def coord_key(x: float, y: float) -> tuple[float, float]:
    """Create a hashable coordinate key."""
    return (snap_coord(x), snap_coord(y))


class UnionFind:
    """Union-Find data structure for net connectivity."""

    def __init__(self):
        self.parent: dict = {}
        self.rank: dict = {}

    def find(self, x):
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1


def build_net_connectivity(leds: list[LedSymbol], wires: list[Wire]) -> dict[str, str]:
    """
    Build net connectivity and return a mapping: DO_ref -> DI_ref.

    This tells us: the DO pin of LED X connects to the DI pin of LED Y,
    meaning X comes before Y in the chain.
    """
    uf = UnionFind()

    # Register all wire endpoints and connect them
    for wire in wires:
        k1 = coord_key(wire.x1, wire.y1)
        k2 = coord_key(wire.x2, wire.y2)
        uf.find(k1)
        uf.find(k2)
        uf.union(k1, k2)

    # Map each net (by root) to the pins connected to it
    # pin_type: 'do' or 'di', with the LED ref
    net_pins: dict[tuple, list[tuple[str, str]]] = {}  # root -> [(ref, 'do'|'di'), ...]

    for led in leds:
        do_key = coord_key(*led.do_pin)
        di_key = coord_key(*led.di_pin)

        # Ensure pin positions are in the UF (they may not touch a wire if
        # they are at a wire endpoint — they should be, but let's be safe)
        uf.find(do_key)
        uf.find(di_key)

        # Connect pin to wires at same position (already handled by UF if
        # the pin coordinate matches a wire endpoint)
        do_root = uf.find(do_key)
        di_root = uf.find(di_key)

        net_pins.setdefault(do_root, []).append((led.ref, 'do'))
        net_pins.setdefault(di_root, []).append((led.ref, 'di'))

    # Build DO→DI connections
    # For each net, if it contains exactly one DO and one DI from different LEDs,
    # that's a chain link.
    do_to_di: dict[str, str] = {}  # ref_from -> ref_to

    for root, pins in net_pins.items():
        do_refs = [ref for ref, ptype in pins if ptype == 'do']
        di_refs = [ref for ref, ptype in pins if ptype == 'di']

        if len(do_refs) == 1 and len(di_refs) == 1:
            from_ref = do_refs[0]
            to_ref = di_refs[0]
            if from_ref != to_ref:  # sanity check
                do_to_di[from_ref] = to_ref
        elif len(do_refs) > 1 or len(di_refs) > 1:
            # Multiple DOs or DIs on same net — unusual, log it
            print(f"  WARNING: Net at root {root} has {len(do_refs)} DO "
                  f"and {len(di_refs)} DI pins: DO={do_refs}, DI={di_refs}",
                  file=sys.stderr)

    return do_to_di


def find_chain_start(leds: list[LedSymbol], do_to_di: dict[str, str],
                     wires: list[Wire], global_label: Optional[str]) -> Optional[str]:
    """
    Find the first LED in the chain — the one whose DI pin is connected to
    the chain's data input (global label / sheet pin) rather than another LED's DO.

    The start LED is the one that appears as a DI target but never as a DO source's target,
    i.e., no other LED's DO connects to its DI.
    """
    # All refs that are targets of a DO connection (i.e. their DI is fed by some LED's DO)
    fed_by_led = set(do_to_di.values())

    # All LED refs
    all_refs = {led.ref for led in leds}

    # Start candidates: LEDs whose DI is NOT fed by another LED's DO
    candidates = all_refs - fed_by_led

    if len(candidates) == 1:
        return candidates.pop()
    elif len(candidates) == 0:
        print(f"  ERROR: No chain start found (circular chain?)", file=sys.stderr)
        return None
    else:
        # Multiple candidates — try to disambiguate
        # This can happen if the sub-sheet has multiple chain segments
        # (e.g., rail_w feeds into rail_w_southbound)
        print(f"  INFO: Multiple chain start candidates: {sorted(candidates)}",
              file=sys.stderr)
        # Return the one with the lowest LED number as a heuristic
        return sorted(candidates, key=lambda r: int(re.search(r'\d+', r).group()))[0]


def walk_chain(start_ref: str, do_to_di: dict[str, str]) -> list[str]:
    """Walk the chain from start, following DO→DI links. Returns ordered list of refs."""
    chain = [start_ref]
    current = start_ref
    visited = {start_ref}

    while current in do_to_di:
        next_ref = do_to_di[current]
        if next_ref in visited:
            print(f"  ERROR: Cycle detected at {current} -> {next_ref}", file=sys.stderr)
            break
        chain.append(next_ref)
        visited.add(next_ref)
        current = next_ref

    return chain


# ---------------------------------------------------------------------------
# Multi-sheet chain handling
# ---------------------------------------------------------------------------

# Map sub-sheet filenames to chain names and expected LED counts
CHAIN_SHEETS = {
    'rail_w': {
        'chain': 'rail_w',
        'sub_sheets': ['rail_w.kicad_sch', 'rail_w_southbound.kicad_sch'],
        'expected_count': 106,
    },
    'rail_e': {
        'chain': 'rail_e',
        'sub_sheets': ['rail_e.kicad_sch'],
        'expected_count': 48,
    },
    'ferries_n': {
        'chain': 'ferries_n',
        'sub_sheets': ['ferries_n.kicad_sch'],
        'expected_count': 46,
    },
    'ferries_s': {
        'chain': 'ferries_s',
        'sub_sheets': ['ferries_s.kicad_sch'],
        'expected_count': 47,
    },
}


def process_chain(chain_name: str, config: dict, project_dir: Path) -> dict:
    """
    Process all sub-sheets for a chain and return the ordered LED list.

    For multi-sheet chains (rail_w + rail_w_southbound), the sheets are
    concatenated: the last LED's DO in sheet 1 connects to the first LED's
    DI in sheet 2 via an inter-sheet label.
    """
    print(f"\n{'='*60}")
    print(f"Processing chain: {chain_name}")
    print(f"{'='*60}")

    all_segments = []

    for sheet_name in config['sub_sheets']:
        sheet_path = project_dir / sheet_name
        if not sheet_path.exists():
            print(f"  WARNING: Sheet {sheet_name} not found, skipping", file=sys.stderr)
            continue

        print(f"\n  Parsing {sheet_name}...")
        leds, wires, global_label = parse_schematic(sheet_path)

        print(f"  Found {len(leds)} LEDs, {len(wires)} wires")
        if global_label:
            print(f"  Global label: '{global_label}'")

        # Print LED details for debugging
        for led in sorted(leds, key=lambda l: int(re.search(r'\d+', l.ref).group())):
            print(f"    {led.ref}: ({led.x}, {led.y}) mirror_y={led.mirror_y} "
                  f"pkg={led.package} DO={led.do_pin} DI={led.di_pin}")

        # Build connectivity
        do_to_di = build_net_connectivity(leds, wires)
        print(f"  DO→DI connections found: {len(do_to_di)}")
        for from_ref, to_ref in sorted(do_to_di.items(),
                                         key=lambda x: int(re.search(r'\d+', x[0]).group())):
            print(f"    {from_ref} → {to_ref}")

        # Find chain start and walk
        start = find_chain_start(leds, do_to_di, wires, global_label)
        if start:
            print(f"  Chain start: {start}")
            ordered = walk_chain(start, do_to_di)
            print(f"  Chain order ({len(ordered)} LEDs): {' → '.join(ordered)}")

            # Build segment data
            led_lookup = {led.ref: led for led in leds}
            segment = []
            for ref in ordered:
                led = led_lookup[ref]
                segment.append({
                    'ref': ref,
                    'package': led.package,
                    'schematic_x': led.x,
                    'schematic_y': led.y,
                })
            all_segments.append({
                'sheet': sheet_name,
                'leds': segment,
            })
        else:
            print(f"  ERROR: Could not determine chain start for {sheet_name}")

    # Concatenate segments for multi-sheet chains
    full_chain = []
    for seg in all_segments:
        full_chain.extend(seg['leds'])

    expected = config['expected_count']
    actual = len(full_chain)
    status = "OK" if actual == expected else f"MISMATCH (expected {expected})"
    print(f"\n  Total LEDs in {chain_name}: {actual} — {status}")

    if actual != expected:
        print(f"  WARNING: Expected {expected} LEDs but found {actual}",
              file=sys.stderr)

    return {
        'chain': chain_name,
        'expected_count': expected,
        'actual_count': actual,
        'segments': [s['sheet'] for s in all_segments],
        'leds': full_chain,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Extract LED chain order from KiCad schematics')
    parser.add_argument('project_dir',
                        help='Path to directory containing .kicad_sch files')
    parser.add_argument('--output', '-o', default='chain_order.json',
                        help='Output JSON file (default: chain_order.json)')
    args = parser.parse_args()

    project_dir = Path(args.project_dir)
    if not project_dir.is_dir():
        print(f"Error: {project_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    # Check which schematic files exist
    all_sheets = set()
    for config in CHAIN_SHEETS.values():
        all_sheets.update(config['sub_sheets'])

    print("Checking for schematic files...")
    for sheet in sorted(all_sheets):
        path = project_dir / sheet
        exists = "✓" if path.exists() else "✗ MISSING"
        print(f"  {sheet}: {exists}")

    # Process each chain
    results = {}
    total_leds = 0

    for chain_name, config in CHAIN_SHEETS.items():
        chain_data = process_chain(chain_name, config, project_dir)
        results[chain_name] = chain_data
        total_leds += chain_data['actual_count']

    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    for chain_name, data in results.items():
        status = "✓" if data['actual_count'] == data['expected_count'] else "✗"
        print(f"  {status} {chain_name}: {data['actual_count']} LEDs "
              f"(expected {data['expected_count']})")
    print(f"  Total: {total_leds} LEDs (expected 247)")

    # Check for duplicate refs across chains
    all_refs = []
    for data in results.values():
        all_refs.extend(led['ref'] for led in data['leds'])
    ref_counts = {}
    for ref in all_refs:
        ref_counts[ref] = ref_counts.get(ref, 0) + 1
    dupes = {ref: count for ref, count in ref_counts.items() if count > 1}
    if dupes:
        print(f"\n  WARNING: Duplicate refs across chains: {dupes}")
    else:
        print(f"\n  ✓ No duplicate refs across chains")

    # Write output
    output = {
        '_comment': 'Generated by 01_extract_chain_order.py — DO NOT EDIT',
        '_total_leds': total_leds,
        'chains': results,
    }

    output_path = Path(args.output)
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"\nOutput written to {output_path}")


if __name__ == '__main__':
    main()