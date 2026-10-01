"""Reject references to names that do not exist (scope-aware undefined names).

Why this exists
---------------
`py_compile` only checks syntax, and importing a module does not execute every
function body — so a typo like using `skip_ex` after renaming the variable to
`skip_ex_mode` passes both. When such a name sits inside a `root.after(...)`
callback, Tk reports the ``NameError`` on stderr and simply drops the callback:
the UI silently stops halfway (the button stayed on「准备中…」in the incident
that prompted this check) instead of failing loudly.

Scope rules matter here: a name may be defined at module level and still be
undefined *inside* the function that uses it. Plain text search cannot tell the
difference, so this walks the AST and resolves each load against its enclosing
scopes — module bindings, enclosing function locals (closures), and builtins.

    python scripts/check_undefined_names.py

Exit code 0 with ``UNDEFINED_NAMES_OK``, or 1 with ``UNDEFINED_NAMES_FINDINGS``.
"""

from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "zephie_rolling_on"
SCRIPTS = ROOT / "scripts"

# Files that legitimately reference names injected by their host runtime.
SKIP_FILES = {
    # PyInstaller runtime hooks get their globals injected by the bootloader.
    SCRIPTS / "pyi_rth_tcl_tk.py",
}

# (relative path, name) → reason. For the rare intentional runtime injection.
ALLOWED: dict[tuple[str, str], str] = {}

_BUILTINS = frozenset(dir(builtins)) | {
    "__name__",
    "__file__",
    "__doc__",
    "__package__",
    "__spec__",
    "__loader__",
    "__builtins__",
    "__debug__",
}


class _Scope:
    """One lexical scope: its bound names, plus any global/nonlocal declarations."""

    __slots__ = ("name", "kind", "bound", "globals", "nonlocals", "parent")

    def __init__(self, name: str, kind: str, parent: "_Scope | None") -> None:
        self.name = name
        self.kind = kind  # "module" | "function" | "class"
        self.bound: set[str] = set()
        self.globals: set[str] = set()
        self.nonlocals: set[str] = set()
        self.parent = parent


def _bind_target(node: ast.AST, into: set[str]) -> None:
    """Collect names bound by an assignment target."""
    if isinstance(node, ast.Name):
        into.add(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for elt in node.elts:
            _bind_target(elt, into)
    elif isinstance(node, ast.Starred):
        _bind_target(node.value, into)
    # Attribute / Subscript targets mutate an object, they bind nothing.


def _expr_bindings(node: ast.AST, into: set[str]) -> None:
    """Collect walrus (``:=``) targets in expressions, skipping nested scopes."""
    for child in ast.iter_child_nodes(node):
        if isinstance(
            child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
        ):
            continue
        if isinstance(child, ast.NamedExpr) and isinstance(child.target, ast.Name):
            into.add(child.target.id)
        _expr_bindings(child, into)


def _bind_stmts(body: list[ast.stmt], scope: _Scope) -> None:
    for stmt in body:
        # 赋值表达式（``:=``）藏在表达式里，不是语句，单独收一遍
        _expr_bindings(stmt, scope.bound)
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope.bound.add(stmt.name)
        elif isinstance(stmt, ast.Import):
            for alias in stmt.names:
                scope.bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(stmt, ast.ImportFrom):
            for alias in stmt.names:
                scope.bound.add(alias.asname or alias.name)
        elif isinstance(stmt, ast.Assign):
            for tgt in stmt.targets:
                _bind_target(tgt, scope.bound)
        elif isinstance(stmt, (ast.AugAssign, ast.AnnAssign)):
            _bind_target(stmt.target, scope.bound)
        elif isinstance(stmt, (ast.For, ast.AsyncFor)):
            _bind_target(stmt.target, scope.bound)
            _bind_stmts(stmt.body, scope)
            _bind_stmts(stmt.orelse, scope)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            for item in stmt.items:
                if item.optional_vars is not None:
                    _bind_target(item.optional_vars, scope.bound)
            _bind_stmts(stmt.body, scope)
        elif isinstance(stmt, ast.Try) or (
            hasattr(ast, "TryStar") and isinstance(stmt, ast.TryStar)
        ):
            _bind_stmts(stmt.body, scope)
            for handler in stmt.handlers:
                if handler.name:
                    scope.bound.add(handler.name)
                _bind_stmts(handler.body, scope)
            _bind_stmts(stmt.orelse, scope)
            _bind_stmts(stmt.finalbody, scope)
        elif isinstance(stmt, ast.Global):
            scope.globals.update(stmt.names)
        elif isinstance(stmt, ast.Nonlocal):
            scope.nonlocals.update(stmt.names)
        elif isinstance(stmt, ast.If):
            _bind_stmts(stmt.body, scope)
            _bind_stmts(stmt.orelse, scope)
        elif isinstance(stmt, (ast.While,)):
            _bind_stmts(stmt.body, scope)
            _bind_stmts(stmt.orelse, scope)
        elif isinstance(stmt, ast.Match):
            for case in stmt.cases:
                # Capture patterns bind names; conservative: walk for Store names.
                for node in ast.walk(case.pattern):
                    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                        scope.bound.add(node.id)
                _bind_stmts(case.body, scope)


def _resolve(scope: _Scope, name: str, module: _Scope) -> bool:
    """Is ``name`` visible from ``scope``?

    Walks the enclosing scopes like Python does:

    * ``global`` declarations send the name to module level — this is the case
      that dominated the first run's false positives (``global _UI`` inside a
      method, with ``_UI`` defined at module level).
    * Closure variables resolve by walking outward, so ``nonlocal`` needs no
      special handling.
    * Class bodies are skipped when resolving from a nested function: Python
      does not expose class attributes to methods that way.
    """
    cur: _Scope | None = scope
    first = True
    while cur is not None:
        if name in cur.globals:
            return name in module.bound
        if name in cur.bound and (first or cur.kind != "class"):
            return True
        first = False
        cur = cur.parent
    return name in module.bound  # module bindings already include builtins


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Return (line, name, scope) for every unresolved load in ``path``."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [(0, f"<unreadable: {type(exc).__name__}>", "")]
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [(exc.lineno or 0, f"<syntax error: {exc.msg}>", "")]

    module = _Scope("<module>", "module", None)
    _bind_stmts(tree.body, module)
    module.bound |= _BUILTINS

    findings: list[tuple[int, str, str]] = []

    def visit(node: ast.AST, scope: _Scope) -> None:
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load) and not _resolve(scope, node.id, module):
                findings.append((node.lineno, node.id, scope.name))
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Decorators / defaults / annotations evaluate in the enclosing scope.
            for dec in node.decorator_list:
                visit(dec, scope)
            args = node.args
            for default in list(args.defaults) + [
                d for d in args.kw_defaults if d is not None
            ]:
                visit(default, scope)
            for a in (
                list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
            ):
                if a.annotation is not None:
                    visit(a.annotation, scope)
            if args.vararg and args.vararg.annotation:
                visit(args.vararg.annotation, scope)
            if args.kwarg and args.kwarg.annotation:
                visit(args.kwarg.annotation, scope)
            if node.returns is not None:
                visit(node.returns, scope)
            # type_params 仅 3.12+ 存在（本包同时支持 3.11）
            for tp in getattr(node, "type_params", None) or ():
                visit(tp, scope)

            inner = _Scope(node.name, "function", scope)
            for a in (
                list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
            ):
                inner.bound.add(a.arg)
            if args.vararg:
                inner.bound.add(args.vararg.arg)
            if args.kwarg:
                inner.bound.add(args.kwarg.arg)
            _bind_stmts(node.body, inner)
            for stmt in node.body:
                visit(stmt, inner)
            return
        if isinstance(node, ast.Lambda):
            args = node.args
            for default in list(args.defaults) + [
                d for d in args.kw_defaults if d is not None
            ]:
                visit(default, scope)
            inner = _Scope("<lambda>", "function", scope)
            for a in (
                list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
            ):
                inner.bound.add(a.arg)
            if args.vararg:
                inner.bound.add(args.vararg.arg)
            if args.kwarg:
                inner.bound.add(args.kwarg.arg)
            visit(node.body, inner)
            return
        if isinstance(node, ast.ClassDef):
            for dec in node.decorator_list:
                visit(dec, scope)
            for base in node.bases + [k.value for k in node.keywords]:
                visit(base, scope)
            inner = _Scope(node.name, "class", scope)
            _bind_stmts(node.body, inner)
            for stmt in node.body:
                visit(stmt, inner)
            return
        if isinstance(
            node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)
        ):
            inner = _Scope("<comprehension>", "function", scope)
            for gen in node.generators:
                _bind_target(gen.target, inner.bound)
            # Walk every child inside the comprehension scope. The first iterable
            # is technically evaluated outside, but treating it as inside only
            # risks missing a finding — it never invents one.
            for child in ast.iter_child_nodes(node):
                visit(child, inner)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    for stmt in tree.body:
        visit(stmt, module)
    return findings


def main() -> int:
    if not SRC.is_dir():
        print(f"source directory not found: {SRC}")
        print("UNDEFINED_NAMES_FINDINGS")
        return 1

    paths: list[Path] = []
    for root in (SRC, SCRIPTS):
        if root.is_dir():
            paths.extend(sorted(root.rglob("*.py")))
    paths = [
        p for p in paths if "__pycache__" not in p.parts and p not in SKIP_FILES
    ]

    findings: list[str] = []
    for path in paths:
        rel = path.relative_to(ROOT).as_posix()
        for line, name, scope in _scan_file(path):
            if (rel, name) in ALLOWED:
                continue
            findings.append(f"{rel}:{line}  {name!r}  (in {scope})")

    print(f"scanned {len(paths)} files for undefined names")
    if findings:
        print()
        for line in findings:
            print(f"  {line}")
        print(f"\ntotal findings: {len(findings)}")
        print("UNDEFINED_NAMES_FINDINGS")
        return 1

    print("no undefined names")
    print("UNDEFINED_NAMES_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
