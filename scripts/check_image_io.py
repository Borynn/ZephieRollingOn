"""Reject raw ``cv2.imread`` / ``cv2.imwrite`` calls.

On Windows these resolve the path through the ANSI code page, so they fail
silently — returning ``None`` / ``False`` — whenever the path holds characters
outside it. A Chinese folder name is enough, and that is exactly how a portable
build gets extracted by hand.

The failure is invisible during development because the source tree usually sits
at an ASCII path, so it only shows up in the packaged build. Use
``vision.image_io.imread_unicode`` / ``imwrite_unicode`` instead.

  python scripts/check_image_io.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "zephie_rolling_on"

# The wrappers themselves are the only legitimate callers.
ALLOWED = {SRC / "vision" / "image_io.py"}

PATTERN = re.compile(r"cv2\.(imread|imwrite)\s*\(")


def main() -> int:
    findings: list[tuple[Path, int, str]] = []
    for path in sorted(SRC.rglob("*.py")):
        if path in ALLOWED:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if PATTERN.search(line):
                findings.append((path, lineno, line.strip()))

    print(f"scanned {len(list(SRC.rglob('*.py')))} modules under src/zephie_rolling_on\n")
    for path, lineno, line in findings:
        print(f"  {path.relative_to(ROOT)}:{lineno}: {line}")
    if findings:
        print(f"\ntotal findings: {len(findings)}")
        print("IMAGE_IO_FINDINGS")
        return 1

    print("no raw cv2 image I/O outside image_io.py")
    print("IMAGE_IO_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
