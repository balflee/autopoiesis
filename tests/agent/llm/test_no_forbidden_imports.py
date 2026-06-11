"""AST scan: ``agent/**`` MUST NOT import ``anthropic`` or ``openai``.

Brief Rule 7 (track-b-backend.md, authoritative):

    Production LLM is **Gemini 3.1 Flash Lite** via ``google-genai``
    SDK + AI Studio (env var ``GEMINI_API_KEY``). [...] **NEVER**
    import ``anthropic`` or ``openai`` in production agent code —
    that's a hard policy violation.

This test is the policy enforcer. A round-1 review of any T-B-*** task
that introduces ``import anthropic`` / ``from openai import ...`` will
fail here BEFORE the specialized reviewer even sees the diff. The
scan is AST-based (not regex) so import statements inside comments /
strings / docstrings do not trip it.

The scan covers EVERY ``.py`` file under ``agent/`` (excluding nothing
— even the LLM package itself MUST be Gemini-only). Test code under
``tests/`` is NOT scanned because tests may legitimately import the
forbidden modules for negative assertions (none currently do, but the
policy is about production code, not tests).
"""

from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_TOP_LEVEL_MODULES = frozenset({"anthropic", "openai"})


def _top_level_module(name: str) -> str:
    """Return the top-level package name of a dotted import.

    Example: ``"anthropic.types"`` → ``"anthropic"``. The forbidden
    list checks the top-level only so a future submodule
    (``anthropic._internal``) does not slip past.
    """
    return name.split(".", 1)[0]


def _walk_imports(tree: ast.AST) -> list[str]:
    """Yield every imported top-level module name from an AST tree.

    Handles both ``import X`` (where X is the name itself) and
    ``from X import Y`` (where X is the module to attribute the
    import to). Relative imports (``from . import x``) are skipped —
    they cannot reference an external package by definition.
    """
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append(_top_level_module(alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level and not node.module:
                # Pure relative import (``from . import x``) — skip.
                continue
            if node.module:
                out.append(_top_level_module(node.module))
    return out


def _scan_file(path: Path) -> list[str]:
    """Return the list of forbidden top-level imports in ``path``."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise AssertionError(f"unable to parse {path}: {exc}") from exc

    imports = _walk_imports(tree)
    return [m for m in imports if m in FORBIDDEN_TOP_LEVEL_MODULES]


def _agent_root() -> Path:
    """Locate the ``agent/`` directory regardless of where pytest was
    invoked. Walks up from this file until ``agent/`` is found at the
    sibling level."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "agent"
        if candidate.is_dir() and (candidate / "__init__.py").exists():
            return candidate
    raise AssertionError("could not locate agent/ package root from test file")


def test_agent_package_does_not_import_anthropic_or_openai() -> None:
    """Scan every .py file under agent/ for the forbidden imports.

    The brief calls this AST scan out explicitly:

        "Import ``anthropic`` or ``openai`` anywhere under
        ``agent/**`` — HARD POLICY VIOLATION (caught by
        ``test_no_forbidden_imports.py`` AST scan; reviewer auto-fails)"

    Any new contributor who reaches for one of these SDKs will hit
    this test in round-1 — before the diff even routes to a reviewer.
    """
    root = _agent_root()
    py_files = list(root.rglob("*.py"))
    assert py_files, f"no .py files found under {root} — scan misconfigured"

    violations: list[str] = []
    for path in py_files:
        found = _scan_file(path)
        if found:
            violations.append(f"{path.relative_to(root.parent)}: {found}")

    assert not violations, (
        "Forbidden LLM SDK import detected under agent/ — track-b-backend.md "
        "Rule 7 mandates Gemini-only. Violations:\n  - "
        + "\n  - ".join(violations)
    )


def test_scan_catches_planted_violation(tmp_path: Path) -> None:
    """Meta-test: the scan WOULD catch a forbidden import if planted.

    A green test result for the agent/ scan is only meaningful if the
    scanner actually trips on the forbidden pattern. We synthesize a
    file with a forbidden import, scan it, and assert the violation
    is reported.
    """
    bad_module = tmp_path / "bad.py"
    bad_module.write_text(
        "from anthropic import Anthropic\nclient = Anthropic()\n",
        encoding="utf-8",
    )
    found = _scan_file(bad_module)
    assert "anthropic" in found

    bad_openai = tmp_path / "bad_openai.py"
    bad_openai.write_text("import openai\n", encoding="utf-8")
    found2 = _scan_file(bad_openai)
    assert "openai" in found2


def test_scan_does_not_false_positive_on_docstrings(tmp_path: Path) -> None:
    """A docstring or comment that mentions ``anthropic`` MUST NOT
    trigger the scan — the AST walker only sees ``Import`` /
    ``ImportFrom`` nodes."""
    clean = tmp_path / "clean.py"
    clean.write_text(
        '"""Docstring mentioning anthropic and openai SDKs."""\n'
        "# Comment also mentioning anthropic, openai\n"
        "x = 'anthropic-string'\n",
        encoding="utf-8",
    )
    found = _scan_file(clean)
    assert found == []


def test_scan_handles_dotted_imports(tmp_path: Path) -> None:
    """A sub-module import like ``import anthropic.types`` MUST be
    caught — the top-level extractor returns ``"anthropic"``."""
    dotted = tmp_path / "dotted.py"
    dotted.write_text("import anthropic.types\n", encoding="utf-8")
    found = _scan_file(dotted)
    assert "anthropic" in found

    from_dotted = tmp_path / "from_dotted.py"
    from_dotted.write_text(
        "from anthropic.types.beta import BetaMessage\n", encoding="utf-8"
    )
    found2 = _scan_file(from_dotted)
    assert "anthropic" in found2
