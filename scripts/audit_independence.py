"""Audit the open-source project for private-context leakage.

The project publishes both source and compiled runtime components, so this
checks two things:

  A. Source text must know nothing about how a package is built or encrypted:
     no research-repo paths, no internal model ids, no build pipeline names,
     no crypto/obfuscation/signing vocabulary, no legacy DLL ABI.

  B. Committed binaries (the shipped runtime, the native engine) must not carry
     private strings: no research-repo path, internal model ids, build pipeline
     names, key file names, or local absolute paths.

Crypto vocabulary is deliberately NOT banned from binaries: the shipped runtime
is the compiled implementation, so it necessarily contains it.

  python scripts/audit_independence.py
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Directories that are not part of the shipped source.
SKIP_DIRS = {
    ".git", "__pycache__", "build", "dist", ".venv", "venv",
    ".pytest_cache", ".mypy_cache", "data",
}

# Files that legitimately contain the patterns: this auditor defines them.
SKIP_FILES = {
    "scripts/audit_independence.py",
    # Generated at runtime by the self-check; gitignored, never published.
    "check_report.txt",
}
SKIP_SUFFIXES = {".egg-info", ".egg-link"}

# (label, regex, why it matters)
RULES: list[tuple[str, str, str]] = [
    ("research repo path", r"AutomaticDiceRolling", "names the private repository"),
    ("research build path", r"m1_decide|m1_distance|full_points", "names internal model ids"),
    ("research toolchain", r"build_win_cpu|build_host\.ps1|build_m1_blackbox|build_vela_package",
     "leaks the private build pipeline"),
    ("container internals", r"RCDATA|res_id|RT_RCDATA|payload\.bin", "leaks package internals"),
    ("crypto vocabulary", r"ChaCha|Poly1305|AEAD|encrypt|decrypt|cipher", "mentions encryption"),
    ("obfuscation", r"obfuscat|mask_seed|mask_key|master\.key|shard|XOR", "mentions obfuscation"),
    ("signing", r"ECDSA|sign_blob|signature bytes|verify_signature", "mentions signing"),
    ("legacy dll path", r"dll_bridge|DllDecisionModel|dm_get_info|dm_import|dm_decide",
     "exposes the legacy DLL ABI"),
]

# A few matches are legitimate; allow them explicitly with a reason.
ALLOWLIST: dict[tuple[str, str], str] = {
    ("src/zephie_rolling_on/decision_models/vela_package.py", "payload_bytes"):
        "field name inside a public .vpk header",
}

# Committed binaries are published too, so scan them for private strings.
BINARY_SUFFIXES = {".exe", ".pyd", ".dll", ".so", ".dylib"}

# (label, regex, why it matters). Crypto vocabulary is intentionally absent:
# the shipped runtime is the compiled implementation of it.
BINARY_RULES: list[tuple[str, bytes, str]] = [
    ("research repo path", rb"AutomaticDiceRolling", "names the private repository"),
    ("internal model ids", rb"m1_distance|full_points|m1_decide|dice_adventure|zephie_",
     "names internal model ids"),
    ("research toolchain", rb"build_m1_blackbox|build_host|pack_model|gen_model_keys|m1_crypto|secure_forward|secure_engine|model_keys",
     "leaks the private build pipeline"),
    ("key material", rb"model_master|model_signing|kMasterBlob|kBlobMaskSeed",
     "names key material"),
    ("local absolute path", rb"[A-Za-z]:\\AI-Agent", "leaks a build-machine path"),
]


def iter_source() -> list[Path]:
    out: list[Path] = []
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if any(part.endswith(tuple(SKIP_SUFFIXES)) for part in p.parts):
            continue
        rel = p.relative_to(ROOT).as_posix()
        if rel in SKIP_FILES:
            continue
        if p.suffix not in {".py", ".ps1", ".md", ".txt", ".yml", ".yaml", ".toml", ".cfg"}:
            continue
        out.append(p)
    return sorted(out)


def iter_binaries() -> list[Path]:
    """Committed compiled artifacts (skips build/ dist/ vendored trees)."""
    out: list[Path] = []
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if any(part.endswith(tuple(SKIP_SUFFIXES)) for part in p.parts):
            continue
        if p.suffix.lower() in BINARY_SUFFIXES:
            out.append(p)
    return sorted(out)


def main() -> int:
    hits: list[tuple[Path, int, str, str, str]] = []
    for p in iter_source():
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for label, pattern, why in RULES:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                line_no = text[: m.start()].count("\n") + 1
                line = text.splitlines()[line_no - 1].strip()
                rel = p.relative_to(ROOT).as_posix()
                if any(rel.endswith(k[0]) and k[1] in line for k in ALLOWLIST):
                    continue
                hits.append((p, line_no, label, why, line))

    print(f"scanned {len(iter_source())} source files under {ROOT.name}\n")
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

    print(f"total findings: {len(hits)}")

    # B. committed binaries
    print()
    bins = iter_binaries()
    print(f"scanned {len(bins)} committed binaries")
    bin_hits: list[tuple[Path, str, str, str]] = []
    for p in bins:
        try:
            data = p.read_bytes()
        except OSError:
            continue
        for label, pattern, why in BINARY_RULES:
            for m in re.finditer(pattern, data):
                bin_hits.append((p, label, why, m.group(0)[:60].decode("latin1")))
    for p, label, why, sample in bin_hits:
        print(f"  {p.relative_to(ROOT)} [{label}] ({why}): {sample!r}")
    if not bin_hits:
        print("  no private strings in committed binaries")
    print(f"binary findings: {len(bin_hits)}")

    ok = not hits and not bin_hits
    print("INDEPENDENCE_OK" if ok else "INDEPENDENCE_FINDINGS")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
