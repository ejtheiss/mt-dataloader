#!/usr/bin/env python3
"""Verify WCAG AA contrast for critical semantic pairs in the dark theme token map.

Reads static/css/theme-tokens.json and checks foreground/background pairs
against AA thresholds (4.5:1 normal text, 3:1 large text / UI components).

Usage:
    python scripts/check_contrast.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MAP_PATH = REPO_ROOT / "static" / "css" / "theme-tokens.json"

AA_NORMAL = 4.5
AA_LARGE = 3.0

PAIRS: list[tuple[str, str, float]] = [
    # (foreground MT name, background MT name, minimum ratio)
    ("off-white", "black",    AA_NORMAL),
    ("off-white", "off-black", AA_NORMAL),
    ("gray-2",    "off-black", AA_NORMAL),
    ("gray-3",    "off-black", AA_LARGE),
    ("blue-2",    "off-black", AA_NORMAL),
    ("green-2",   "off-black", AA_NORMAL),
    ("orange-2",  "off-black", AA_NORMAL),
    ("gold-2",    "off-black", AA_NORMAL),
    ("white",     "green-3",   AA_NORMAL),
    ("white",     "blue-3",    AA_NORMAL),
    ("green-1",   "green-4",   AA_NORMAL),
    ("orange-1",  "orange-4",  AA_NORMAL),
    ("gold-1",    "gold-4",    AA_NORMAL),
    ("blue-1",    "blue-4",    AA_NORMAL),
    ("violet-1",  "violet-4",  AA_NORMAL),
    ("purple-1",  "purple-4",  AA_NORMAL),
    ("gray-1",    "gray-4",    AA_NORMAL),
]


def _hex_to_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))


def _linearize(c: float) -> float:
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(hex_color: str) -> float:
    r, g, b = (_linearize(c) for c in _hex_to_rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    l1 = _luminance(fg)
    l2 = _luminance(bg)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def main() -> int:
    data = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    palette = data.get("palette", {}).get("mt") or data.get("mt_palette", {})
    if not palette:
        print("error: cannot find MT palette in token map", file=sys.stderr)
        return 1
    failures: list[str] = []

    for fg_name, bg_name, threshold in PAIRS:
        fg_hex = palette[fg_name]
        bg_hex = palette[bg_name]
        ratio = contrast_ratio(fg_hex, bg_hex)
        status = "PASS" if ratio >= threshold else "FAIL"
        line = f"  {fg_name:12} on {bg_name:12}: {ratio:5.2f}:1  (need {threshold}:1) {status}"
        print(line)
        if status == "FAIL":
            failures.append(line)

    if failures:
        print(f"\n{len(failures)} contrast pair(s) failed AA threshold.", file=sys.stderr)
        return 1
    print(f"\nAll {len(PAIRS)} pairs pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
