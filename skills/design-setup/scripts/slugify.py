#!/usr/bin/env python3
"""
Convert a brand name to a filesystem-safe slug for preset filenames.

Usage:
    python slugify.py "TEKION"               -> tekion
    python slugify.py "Vibe Coder"           -> vibe-coder
    python slugify.py "株式会社ABC"          -> abc
    python slugify.py "あいうえお Inc."      -> inc
    python slugify.py "TEKION株式会社"       -> tekion

Rules:
    - Lowercase
    - Strip non-ASCII (Japanese / CJK / accented chars dropped, NOT romanized)
    - Replace whitespace and underscores with hyphens
    - Drop everything except [a-z0-9-]
    - Collapse runs of hyphens, trim leading/trailing hyphens
    - If result is empty (e.g. all Japanese), fall back to "brand"
"""

import re
import sys


def slugify(value):
    ascii_only = value.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    hyphenated = re.sub(r"[\s_]+", "-", lowered)
    cleaned = re.sub(r"[^a-z0-9-]", "", hyphenated)
    collapsed = re.sub(r"-+", "-", cleaned).strip("-")
    return collapsed or "brand"


def main():
    if len(sys.argv) != 2:
        print("Usage: slugify.py <brand-name>", file=sys.stderr)
        sys.exit(2)
    print(slugify(sys.argv[1]))


if __name__ == "__main__":
    main()
