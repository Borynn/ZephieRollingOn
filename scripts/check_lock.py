#!/usr/bin/env python3
"""Verify ``requirements.lock`` still satisfies ``requirements.txt``.

Why this exists
---------------
``requirements.txt`` declares only lower bounds (``numpy>=1.24.0``), so a build
resolves to whatever is newest at that moment. ``requirements.lock`` pins the
exact tree so CI and local builds agree. The two can silently drift apart: bump
a floor in ``requirements.txt`` and forget to regenerate the lock, and CI would
keep installing the old pin while claiming to satisfy the file.

This gate fails when:
  - a requirement in ``requirements.txt`` has no pin in the lock
  - a pin is lower than a declared floor
  - a lock line is not a usable ``name==version`` pin

Deliberately written in Python rather than PowerShell: the comparison needs
PEP 440 semantics, and PowerShell's ``[version]`` rejects single-segment versions
(``312``) and ``[0]`` on a scalar string returns a char, which made an earlier
inline attempt compare the wrong things.

  python scripts/check_lock.py
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.utils import canonicalize_name
    from packaging.version import InvalidVersion, Version
except ImportError:  # pragma: no cover
    print("check_lock: `packaging` is required (it is pinned in requirements.lock)")
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
REQ_PATH = ROOT / "requirements.txt"
LOCK_PATH = ROOT / "requirements.lock"


def read_pins(path: Path) -> tuple[dict[str, str], list[str]]:
    """Return ({canonical name: version}, malformed lines)."""
    pins: dict[str, str] = {}
    malformed: list[str] = []
    if not path.is_file():
        raise SystemExit(f"missing {path.name}")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-") or "@" in line:
            # An editable/VCS line or a direct URL: never valid in a portable lock.
            malformed.append(line)
            continue
        name, sep, ver = line.partition("==")
        if not sep or not name or not ver:
            malformed.append(line)
            continue
        try:
            Version(ver)
        except InvalidVersion:
            malformed.append(line)
            continue
        pins[canonicalize_name(name)] = ver
    return pins, malformed


def read_requirements(path: Path) -> list[Requirement]:
    out: list[Requirement] = []
    if not path.is_file():
        raise SystemExit(f"missing {path.name}")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        try:
            out.append(Requirement(line))
        except InvalidRequirement as exc:
            print(f"  WARN: cannot parse requirement {line!r}: {exc}")
    return out


def main() -> int:
    pins, malformed = read_pins(LOCK_PATH)
    reqs = read_requirements(REQ_PATH)

    print(f"  {LOCK_PATH.name}: {len(pins)} pins")
    print(f"  {REQ_PATH.name}: {len(reqs)} requirements")

    problems: list[str] = [f"unusable lock line: {m}" for m in malformed]

    for req in reqs:
        # Skip requirements whose environment marker does not apply here (the
        # lock is generated for Windows, which is also where builds run).
        if req.marker is not None and not req.marker.evaluate():
            print(f"  skip (marker not active): {req.name}")
            continue

        key = canonicalize_name(req.name)
        pin = pins.get(key)
        if pin is None:
            problems.append(f"{req.name}: not pinned in {LOCK_PATH.name}")
            continue

        pinned = Version(pin)
        for spec in req.specifier:
            if spec.operator in (">=", "==", "~=", "==="):
                target = Version(spec.version)
                ok = (
                    pinned >= target
                    if spec.operator == ">="
                    else pinned == target
                )
                if not ok:
                    problems.append(
                        f"{req.name}: pinned {pin} does not satisfy "
                        f"declared {spec.operator}{spec.version}"
                    )
            elif spec.operator in ("<", "<=", "!="):
                # Upper bounds are not currently used; flag so they are not
                # silently ignored if someone adds one.
                problems.append(
                    f"{req.name}: lock check does not handle upper bound "
                    f"{spec.operator}{spec.version}"
                )

    if problems:
        print()
        print(f"  {LOCK_PATH.name} is out of sync with {REQ_PATH.name}:")
        for p in problems:
            print(f"    - {p}")
        print()
        print("  Regenerate the lock, then re-apply its two documented fixes")
        print("  (drop the project's editable line and any conda `file:///` entry):")
        print("    python -m pip freeze")
        print()
        print("LOCK_OUT_OF_SYNC")
        return 1

    print()
    print("  lock satisfies every declared floor")
    print("LOCK_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
