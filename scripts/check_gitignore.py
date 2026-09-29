"""Check that .gitignore entries behave as intended, without needing git.

Implements the small subset of gitignore semantics we rely on:
  - last matching pattern wins
  - `!pattern` re-includes
  - `*` does not cross `/`
  - `*.py[cod]` is a character class

Rationale: `*.py[cod]` also matches `.pyd`, so the native engine would silently
be excluded from the repo unless an explicit negation follows it.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (path, should_be_ignored)
CASES: list[tuple[str, bool]] = [
    # Open-source packages stay out of the repo; closed ones are tracked.
    ("decision_models/vela_v4.vpk", True),
    # Shipped runtime component and closed packages are part of the repo.
    ("model_host.exe", False),
    ("decision_models/zephie_example.zm", False),
    # Build artifacts, regenerated on each build.
    ("ZephieRollingOn!.spec", True),
    ("map.xlsx.cache.json", True),
    ("src/zephie_rolling_on.egg-info/PKG-INFO", True),
    # Source and the native engine must stay in.
    ("native/vela/vela_official.cp311-win_amd64.pyd", False),
    ("native/vela/README.md", False),
    ("src/zephie_rolling_on/decision_models/vela_package.py", False),
    ("src/zephie_rolling_on/__pycache__/x.cpython-311.pyc", True),
    # Local / user state (created at runtime)
    ("config/ui_geometry.yaml", True),
    ("config/auto_click.yaml", True),
    ("config/auto_click.default.yaml", False),
    ("config/game_window.yaml", True),
    ("config/planner_state.yaml", True),
    ("config/hotkeys.yaml", True),
    ("config/dev.yaml", False),
    ("README.md", False),
    ("PROJECT_STRUCTURE.md", False),
]


def load_rules() -> list[tuple[str, bool]]:
    rules: list[tuple[str, bool]] = []
    for raw in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        pat = line[1:] if negate else line
        pat = pat.rstrip("/")
        rules.append((pat, negate))
    return rules


def matches(path: str, pat: str) -> bool:
    """Rough gitignore match: try the whole path and every suffix."""
    if "/" in pat:
        return fnmatch.fnmatchcase(path, pat) or path.startswith(pat + "/")
    parts = path.split("/")
    return any(fnmatch.fnmatchcase(p, pat) for p in parts)


def ignored(path: str, rules: list[tuple[str, bool]]) -> bool:
    state = False
    for pat, negate in rules:
        if matches(path, pat):
            state = not negate
    return state


def main() -> int:
    rules = load_rules()
    print(f"{len(rules)} active rules in .gitignore\n")
    fails = 0
    for path, want in CASES:
        got = ignored(path, rules)
        ok = got == want
        fails += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FAIL'} {path:52} ignored={got} (want {want})")
    print("\nGITIGNORE_OK" if fails == 0 else f"\nGITIGNORE_FAILED ({fails})")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
