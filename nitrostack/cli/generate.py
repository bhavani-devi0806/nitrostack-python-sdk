"""Code generation for `nitrostack-py generate`."""

from __future__ import annotations

import os
import re
import sys
from typing import Dict, Optional

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")

COMPONENT_KINDS = ("guard", "pipe", "interceptor", "filter", "service")

_KIND_DIR: Dict[str, str] = {
    "guard": "guards",
    "pipe": "pipes",
    "interceptor": "interceptors",
    "filter": "filters",
    "service": "services",
}

_KIND_SUFFIX: Dict[str, str] = {
    "guard": "Guard",
    "pipe": "Pipe",
    "interceptor": "Interceptor",
    "filter": "Filter",
    "service": "Service",
}


def to_pascal_case(name: str) -> str:
    cleaned = name.replace("-", "_")
    if "_" in cleaned:
        return "".join(part.capitalize() for part in cleaned.split("_") if part)
    if cleaned and cleaned[0].isupper():
        return cleaned
    return cleaned[:1].upper() + cleaned[1:] if cleaned else cleaned


def to_snake_case(name: str) -> str:
    cleaned = name.replace("-", "_")
    if "_" in cleaned:
        return re.sub(r"_+", "_", cleaned).strip("_").lower()
    stepped = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", cleaned)
    stepped = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", stepped)
    return stepped.lower()


def class_name_for(kind: str, name: str) -> str:
    pascal = to_pascal_case(name)
    suffix = _KIND_SUFFIX[kind]
    if pascal.endswith(suffix):
        return pascal
    return f"{pascal}{suffix}"


def _load_template(filename: str) -> str:
    path = os.path.join(TEMPLATES_DIR, filename)
    if not os.path.exists(path):
        print(f"Error: template '{filename}' not found at '{path}'.")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _write_file(path: str, content: str) -> None:
    if os.path.exists(path):
        print(f"Error: File '{path}' already exists.")
        sys.exit(1)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
        if not content.endswith("\n"):
            handle.write("\n")


def generate_component(kind: str, name: str, cwd: Optional[str] = None) -> str:
    """Render a component template and write it under the project's expected directory."""
    if kind not in COMPONENT_KINDS:
        print(f"Error: unknown generate target '{kind}'.")
        sys.exit(1)
    if not name or not re.match(r"^[A-Za-z_][A-Za-z0-9_-]*$", name):
        print("Error: name must be a valid identifier (letters, numbers, '_' or '-').")
        sys.exit(1)

    root = cwd or os.getcwd()
    class_name = class_name_for(kind, name)
    snake = to_snake_case(name)
    rel_path = os.path.join(_KIND_DIR[kind], f"{snake}.py")
    dest = os.path.join(root, rel_path)

    content = _load_template(f"{kind}.py").replace("CLASS_NAME", class_name)
    _write_file(dest, content)
    print(f"Generated {kind} boilerplate in '{rel_path}'")
    return dest


def generate_module(name: str, cwd: Optional[str] = None) -> str:
    """Preserve existing `generate module` behavior: `{name}_module.py` in CWD."""
    root = cwd or os.getcwd()
    filename = f"{name}_module.py"
    dest = os.path.join(root, filename)
    camel_name = "".join(part.capitalize() for part in name.split("_"))
    content = _load_template("module.py").format(name=name, camel_name=camel_name)
    _write_file(dest, content)
    print(f"Generated module boilerplate in '{filename}'")
    return dest
