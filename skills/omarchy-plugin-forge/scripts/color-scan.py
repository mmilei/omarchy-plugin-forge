#!/usr/bin/env python3
"""Fail on hardcoded colors in a plugin's QML.

Every color has to come from the theme (Commons.Color, Commons.Style,
Commons.Util.alpha, or colors.toml read at runtime) so the plugin follows
omarchy-theme-set. Flags hex literals, named colors and Qt.rgba/hsla/hsva
with constant arguments. "transparent" is allowed.

Usage: color-scan.py <plugin-dir>   (exit 1 and one line per hit)
"""
import pathlib
import re
import sys

NAMED = (
    "black|white|red|green|blue|yellow|orange|purple|magenta|cyan|gray|grey|"
    "pink|brown|lime|navy|teal|olive|maroon|silver|gold|violet|indigo"
)
PATTERNS = [
    ("hex literal", re.compile(r"""["']#[0-9a-fA-F]{3,8}["']""")),
    ("named color", re.compile(r"""["'](?:%s)["']""" % NAMED, re.IGNORECASE)),
    ("constant Qt color", re.compile(r"Qt\.(?:rgba|hsla|hsva)\(\s*[\d.]+\s*,\s*[\d.]+\s*,\s*[\d.]+")),
]


def strip_comment(line):
    # Good enough for QML: drop // comments that are not inside a string.
    in_str = None
    for i, ch in enumerate(line):
        if in_str:
            if ch == in_str and line[i - 1] != "\\":
                in_str = None
        elif ch in "\"'":
            in_str = ch
        elif line.startswith("//", i):
            return line[:i]
    return line


def scan(root):
    hits = []
    for path in sorted(pathlib.Path(root).rglob("*.qml")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = strip_comment(line)
            for label, pattern in PATTERNS:
                if pattern.search(code):
                    if label == "constant Qt color" and re.search(r",\s*0(?:\.0*)?\s*\)", code):
                        continue  # fully transparent
                    hits.append(f"{path.relative_to(root)}:{n}: {label}: {line.strip()}")
    return hits


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    found = scan(sys.argv[1])
    for hit in found:
        print(hit)
    sys.exit(1 if found else 0)
