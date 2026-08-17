"""Shared CLI helpers for pack, upgrade, and validate.

One encoding, one dependency-array parser, one requirement-name normalizer,
and one exclude-dir set — so the commands cannot drift apart.
"""

from __future__ import annotations

import os
import re
import tempfile
from typing import List, Optional, Sequence, Tuple

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


EXCLUDE_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    ".env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".cache",
    "node_modules",
    ".next",
    "dist",
    "build",
    ".eggs",
    ".idea",
    ".vscode",
}

_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


def read_text(path: str) -> str:
    """Read a text file, stripping a UTF-8 BOM when present."""
    with open(path, "r", encoding="utf-8-sig") as handle:
        return handle.read()


def write_text_atomic(path: str, content: str) -> None:
    """Write ``content`` via a same-directory temp file, then ``os.replace``.

    If anything fails before replace, the original file is left untouched.
    """
    directory = os.path.dirname(os.path.abspath(path)) or os.getcwd()
    fd, tmp_path = tempfile.mkstemp(prefix=".nitrostack-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def find_toml_array_inner(text: str, key: str = "dependencies") -> Optional[Tuple[int, int]]:
    """Return ``(inner_start, inner_end)`` of ``key = [ ... ]``, or None.

    Bracket depth is tracked and string literals are ignored, so extras such as
    ``uvicorn[standard]`` do not terminate the array.
    """
    match = re.search(rf"^{re.escape(key)}\s*=\s*\[", text, re.MULTILINE)
    if not match:
        return None
    open_bracket = match.end() - 1
    depth = 1
    in_str: Optional[str] = None
    escape = False
    i = open_bracket + 1
    while i < len(text):
        ch = text[i]
        if in_str is not None:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_str:
                in_str = None
        else:
            if ch in ("'", '"'):
                in_str = ch
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return open_bracket + 1, i
        i += 1
    return None


def parse_toml_string_array_items(inner: str) -> List[str]:
    """Parse items from the inside of a TOML string array."""
    items: List[str] = []
    i = 0
    n = len(inner)
    while i < n:
        while i < n and inner[i] in " \t\r\n,":
            i += 1
        if i >= n:
            break
        if inner[i] == "#":
            while i < n and inner[i] != "\n":
                i += 1
            continue
        if inner[i] in ("'", '"'):
            quote = inner[i]
            i += 1
            start = i
            escape = False
            while i < n:
                ch = inner[i]
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == quote:
                    items.append(inner[start:i])
                    i += 1
                    break
                i += 1
            else:
                items.append(inner[start:].strip())
        else:
            start = i
            while i < n and inner[i] not in ",#\n":
                i += 1
            item = inner[start:i].strip().strip("\"'")
            if item:
                items.append(item)
    return items


def parse_pyproject_dependencies(text: str) -> List[str]:
    span = find_toml_array_inner(text, "dependencies")
    if not span:
        return []
    inner_start, inner_end = span
    return parse_toml_string_array_items(text[inner_start:inner_end])


def parse_requirements_text(text: str) -> List[str]:
    deps: List[str] = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("\ufeff").strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        deps.append(line)
    return deps


def parse_requirements(path: str) -> List[str]:
    return parse_requirements_text(read_text(path))


def requirement_name(req: str) -> str:
    """PEP 503-ish distribution name (before extras or version)."""
    text = req.lstrip("\ufeff").strip()
    match = _NAME_RE.match(text)
    if not match:
        return text.lower()
    return re.sub(r"[-_.]+", "-", match.group(1)).strip("-").lower()


def split_requirement(req: str) -> Tuple[str, str]:
    """Return ``(normalized_name, version_spec)``; extras are stripped from the spec."""
    text = req.lstrip("\ufeff").strip()
    match = _NAME_RE.match(text)
    if not match:
        return text.lower(), ""
    name = requirement_name(match.group(1))
    rest = text[match.end() :].strip()
    extras = re.match(r"^(\[[^\]]*\])\s*(.*)$", rest)
    if extras:
        rest = extras.group(2).strip()
    return name, rest


def loads_toml(text: str) -> dict:
    return tomllib.loads(text)


def replace_toml_array_inner(text: str, key: str, new_inner: str) -> str:
    span = find_toml_array_inner(text, key)
    if not span:
        raise ValueError(f"No {key} = [ ... ] array found")
    inner_start, inner_end = span
    return text[:inner_start] + new_inner + text[inner_end:]


def add_to_toml_string_array(text: str, spec: str, key: str = "dependencies") -> str:
    """Append a quoted string item to a TOML array without disturbing existing items."""
    span = find_toml_array_inner(text, key)
    if not span:
        return text
    inner_start, inner_end = span
    inner = text[inner_start:inner_end]
    indent = "    "
    addition = f'\n{indent}"{spec}",\n'
    if inner.strip():
        if not inner.rstrip().endswith(","):
            addition = f',\n{indent}"{spec}",\n'
        else:
            addition = f'{indent}"{spec}",\n'
        new_inner = inner + ("" if inner.endswith("\n") else "\n") + addition
    else:
        new_inner = addition
    return text[:inner_start] + new_inner + text[inner_end:]


def iter_excluded_dirnames(dirnames: Sequence[str]) -> List[str]:
    return [
        d for d in dirnames
        if d not in EXCLUDE_DIR_NAMES and not d.endswith(".egg-info")
    ]
