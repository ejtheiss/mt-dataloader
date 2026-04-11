#!/usr/bin/env python3
"""Fail if strict-listed CSS files contain bare hex colors (theme centralization guard).

See docs/VISUAL_LAYER.md. Extend ``STRICT_CSS_FILES`` as more modules go semantic-only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Paths relative to repo root — keep small; expand deliberately.
STRICT_CSS_FILES = (
    "static/css/page-chrome.css",
    "static/css/layout.css",
    "static/css/index-table.css",
    "static/css/kv-table.css",
    "static/css/filter-bar.css",
    "static/css/pagination.css",
    "static/css/drawer.css",
    "static/css/toast.css",
    "static/css/buttons.css",
    "static/css/forms.css",
    "static/css/case-card.css",
    "static/css/status-indicator.css",
    "static/css/chip.css",
    "static/css/json-view.css",
    "static/css/tabs.css",
    "static/css/toggle-switch.css",
    "static/css/pill.css",
    "static/mt-patterns.css",
)

# Match #rgb, #rrggbb, #rrggbbaa (not # in id selectors — line must look like a color use)
HEX_COLOR = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b")


def _strip_css_comments(text: str) -> str:
    out = []
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end == -1:
                break
            out.append(" " * (end + 2 - i))
            i = end + 2
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def main() -> int:
    bad: list[str] = []
    for rel in STRICT_CSS_FILES:
        path = REPO_ROOT / rel
        if not path.is_file():
            bad.append(f"missing strict file: {rel}")
            continue
        text = _strip_css_comments(path.read_text(encoding="utf-8"))
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("//") or not stripped:
                continue
            if "literal:" in line:
                continue
            if HEX_COLOR.search(line):
                bad.append(f"{rel}:{lineno}: {line.strip()[:120]}")
    if bad:
        print("check_css_literals: bare hex found in strict CSS files:\n", file=sys.stderr)
        for b in bad:
            print(b, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
