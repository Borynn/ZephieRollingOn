"""Check the published tree for private-context leakage.

Two things are checked:

  A. **Source text** — published sources must not describe the internal packaging
     pipeline or its internals.
  B. **Committed binaries** — the shipped runtime components are published too,
     so they must not carry private strings.

The banned-string list lives in ``scripts/audit_terms.local.py``, which is
**gitignored on purpose**: the list is itself the sensitive part, so keeping it
in a published file would defeat the check. Without that file the gate reports
that the pattern rules were skipped and exits 0 — a fresh clone has nothing
private to leak, so this degrades safely rather than blocking contributors.

  python scripts/audit_independence.py
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Local, never-committed term list (see the module docstring).
TERMS_PATH = ROOT / "scripts" / "audit_terms.local.py"

# Directories that are not part of the shipped source.
SKIP_DIRS = {
    ".git", "__pycache__", "build", "dist", ".venv", "venv",
    ".pytest_cache", ".mypy_cache", "data",
}
SKIP_SUFFIXES = {".egg-info", ".egg-link"}

SOURCE_SUFFIXES = {".py", ".ps1", ".md", ".txt", ".yml", ".yaml", ".toml", ".cfg"}


def _load_terms():
    """Load the local term list, or ``None`` when it is absent."""
    if not TERMS_PATH.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_audit_terms_local", TERMS_PATH)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def iter_source() -> list[Path]:
    out: list[Path] = []
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if any(part.endswith(tuple(SKIP_SUFFIXES)) for part in p.parts):
            continue
        if p.name.endswith(".local.py"):
            continue
        if p.suffix not in SOURCE_SUFFIXES:
            continue
        out.append(p)
    return sorted(out)


def iter_binaries(binary_suffixes: set[str]) -> list[Path]:
    out: list[Path] = []
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if any(part.endswith(tuple(SKIP_SUFFIXES)) for part in p.parts):
            continue
        if p.suffix.lower() in binary_suffixes:
            out.append(p)
    return sorted(out)


def _scan_source(rules, allowlist) -> list[tuple[Path, int, str, str, str]]:
    hits = []
    for p in iter_source():
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for label, pattern, why in rules:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                line_no = text[: m.start()].count("\n") + 1
                line = text.splitlines()[line_no - 1].strip()
                rel = p.relative_to(ROOT).as_posix()
                if any(rel.endswith(k[0]) and k[1] in line for k in allowlist):
                    continue
                hits.append((p, line_no, label, why, line))
    return hits


def _scan_binaries(files, rules) -> list[tuple[Path, str, str, str]]:
    hits = []
    for p in files:
        try:
            data = p.read_bytes()
        except OSError:
            continue
        for label, pattern, why in rules:
            for m in re.finditer(pattern, data):
                hits.append((p, label, why, m.group(0)[:60].decode("latin1")))
    return hits


def main() -> int:
    terms = _load_terms()

    src_files = iter_source()
    print(f"scanned {len(src_files)} source files under {ROOT.name}")

    if terms is None:
        print()
        print(f"  NOTE: {TERMS_PATH.name} not found — pattern rules skipped.")
        print("        That file is gitignored; a published clone has no private")
        print("        strings to leak, so this is expected outside a dev checkout.")
        print()
        print("INDEPENDENCE_SKIPPED")
        return 0

    hits = _scan_source(terms.RULES, getattr(terms, "ALLOWLIST", {}))
    if not hits:
        print("no leaks found")
    else:
        by_label: dict[str, list[tuple[Path, int, str, str]]] = {}
        for p, ln, label, why, line in hits:
            by_label.setdefault(label, []).append((p, ln, why, line))
        for label, rows in sorted(by_label.items()):
            print(f"[{label}]  ({rows[0][2]})")
            for p, ln, _why, line in rows:
                print(f"  {p.relative_to(ROOT)}:{ln}: {line[:100]}")
            print()

    print()
    bins = iter_binaries(terms.BINARY_SUFFIXES)
    print(f"scanned {len(bins)} committed binaries")
    bin_hits = _scan_binaries(bins, terms.BINARY_RULES)
    for p, label, why, sample in bin_hits:
        print(f"  {p.relative_to(ROOT)} [{label}] ({why}): {sample!r}")
    if not bin_hits:
        print("  no private strings in committed binaries")

    print()
    ok = not hits and not bin_hits
    print("INDEPENDENCE_OK" if ok else "INDEPENDENCE_FINDINGS")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
