#!/usr/bin/env python3
"""
Derive Primary Light / Primary Dark from a Primary HEX color.

Usage:
    python derive_palette.py "#EA5514"

Output (JSON to stdout):
    {"primary": "#EA5514", "primary_light": "#FEF0E8", "primary_dark": "#BB4410"}

The Primary Light is mixed 90% toward white so black text on top stays
readable (card backgrounds, highlight boxes). The Primary Dark is mixed
20% toward black for high-contrast surfaces like buttons that carry
white text.
"""

import json
import re
import sys


HEX_PATTERN = re.compile(r"^#?([0-9a-fA-F]{6})$")


def parse_hex(value):
    match = HEX_PATTERN.match(value.strip())
    if not match:
        raise ValueError(f"Invalid HEX color: {value!r} (expected #RRGGBB)")
    digits = match.group(1)
    return tuple(int(digits[i : i + 2], 16) for i in (0, 2, 4))


def to_hex(rgb):
    return "#" + "".join(f"{max(0, min(255, round(c))):02X}" for c in rgb)


def mix(rgb, target, ratio):
    return tuple(rgb[i] + (target[i] - rgb[i]) * ratio for i in range(3))


def derive(primary_hex):
    primary = parse_hex(primary_hex)
    light = mix(primary, (255, 255, 255), 0.90)
    dark = mix(primary, (0, 0, 0), 0.20)
    return {
        "primary": to_hex(primary),
        "primary_light": to_hex(light),
        "primary_dark": to_hex(dark),
    }


def main():
    if len(sys.argv) != 2:
        print("Usage: derive_palette.py <#RRGGBB>", file=sys.stderr)
        sys.exit(2)
    try:
        result = derive(sys.argv[1])
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
