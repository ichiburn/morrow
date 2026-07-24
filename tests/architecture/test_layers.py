"""Enforce the layer dependency matrix declared in docs/architecture/operations.md.

Scope of this guarantee, stated honestly: this is a check on **static imports**.
It walks the AST of every module under ``src/morrow`` and rejects any ``import`` or
``from ... import`` whose target is not on that layer's allowlist. It additionally
rejects dynamic import calls, because those would route around the allowlist.

It does **not** catch indirect calls through attributes already bound elsewhere, nor
``eval``. A layer violation smuggled in that way will not be reported here.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "morrow"

# Module roots each layer may import. A prefix match on the dotted path.
# Anything not listed is a violation — this is an allowlist, not a denylist,
# so a newly introduced I/O library fails the test instead of slipping through.
_STDLIB_PURE = frozenset(
    {
        "collections",
        "dataclasses",
        "decimal",
        "enum",
        "functools",
        "hashlib",
        "itertools",
        "math",
        "statistics",
        "typing",
        "__future__",
    }
)

ALLOWED: dict[str, frozenset[str]] = {
    "domain": _STDLIB_PURE | {"morrow.domain", "pydantic"},
    "application": _STDLIB_PURE
    | {
        "morrow.domain",
        "morrow.application",
        "pydantic",
        "abc",
        "asyncio",
        "contextlib",
    },
    # Adapters are the only place the outside world is allowed in.
    "adapters": frozenset(),  # empty set means "no restriction"; see _is_allowed
    "cli": frozenset(),
}

# Import machinery that would bypass a static allowlist.
DYNAMIC_IMPORT_NAMES = frozenset({"__import__", "import_module"})


def _layer_of(path: Path) -> str | None:
    """Return the layer a source file belongs to, or None if it is outside one."""
    rel = path.relative_to(SRC)
    return rel.parts[0] if len(rel.parts) > 1 else None


def _is_allowed(layer: str, module: str) -> bool:
    allowed = ALLOWED[layer]
    if not allowed:  # adapters / cli: unrestricted by design
        return True
    root = module.split(".")[0]
    return any(module == a or module.startswith(f"{a}.") or root == a for a in allowed)


def _imported_modules(tree: ast.AST) -> Iterator[tuple[str, int]]:
    """Yield (dotted module path, line number) for every static import."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            # level > 0 is a relative import: it stays inside the current package,
            # so it can only reach the layer it already belongs to.
            if node.level == 0 and node.module:
                yield node.module, node.lineno


def _dynamic_imports(tree: ast.AST) -> Iterator[tuple[str, int]]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        if name in DYNAMIC_IMPORT_NAMES:
            yield name, node.lineno


def _source_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if _layer_of(p) in ALLOWED)


@pytest.mark.parametrize("path", _source_files(), ids=lambda p: str(p.relative_to(SRC)))
def test_module_only_imports_its_layer_allowlist(path: Path) -> None:
    layer = _layer_of(path)
    assert layer is not None
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    violations = [
        f"{path.relative_to(SRC)}:{lineno} imports {module!r}, "
        f"which is not on the {layer!r} allowlist"
        for module, lineno in _imported_modules(tree)
        if not _is_allowed(layer, module)
    ]
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("path", _source_files(), ids=lambda p: str(p.relative_to(SRC)))
def test_module_does_not_import_dynamically(path: Path) -> None:
    """Dynamic imports would route around the allowlist above."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations = [
        f"{path.relative_to(SRC)}:{lineno} calls {name}()"
        for name, lineno in _dynamic_imports(tree)
    ]
    assert not violations, "\n".join(violations)


def test_every_layer_directory_is_covered() -> None:
    """A new top-level package under src/morrow must declare its allowlist.

    Without this, adding ``src/morrow/whatever/`` would silently escape the matrix.
    """
    declared = set(ALLOWED)
    actual = {p.name for p in SRC.iterdir() if p.is_dir() and not p.name.startswith("_")}
    assert actual <= declared, f"layers without an allowlist entry: {sorted(actual - declared)}"
