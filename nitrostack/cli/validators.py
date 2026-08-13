"""Project validation for `nitrostack-py validate`."""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import sys
import traceback
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence, Set, Tuple

_EXCLUDE_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".next",
    "dist",
    "build",
    ".eggs",
    ".tox",
    ".nox",
}


@dataclass
class ValidationIssue:
    severity: str  # "error" or "warning"
    message: str
    hint: str = ""
    path: str = ""

    def format(self) -> str:
        location = f"{self.path}: " if self.path else ""
        text = f"[{self.severity.upper()}] {location}{self.message}"
        if self.hint:
            text += f"\n        → {self.hint}"
        return text


def _iter_python_files(root: str) -> Iterable[str]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in _EXCLUDE_DIR_NAMES and not d.endswith(".egg-info")
        ]
        for filename in filenames:
            if filename.endswith(".py"):
                yield os.path.join(dirpath, filename)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _parse_pyproject_dependencies(text: str) -> List[str]:
    match = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.MULTILINE | re.DOTALL)
    if not match:
        return []
    deps: List[str] = []
    for raw in match.group(1).split(","):
        item = raw.strip().strip(",").strip()
        if not item or item.startswith("#"):
            continue
        deps.append(item.strip("\"'"))
    return deps


def _parse_requirements(path: str) -> List[str]:
    deps: List[str] = []
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            deps.append(line)
    return deps


def _split_req(req: str) -> Tuple[str, str]:
    match = re.match(r"^\s*([A-Za-z0-9_.-]+)\s*(.*)$", req)
    if not match:
        return req.strip().lower(), ""
    return match.group(1).lower().replace("_", "-"), match.group(2).strip()


def _parse_version_constraint(spec: str) -> Optional[Tuple[str, str]]:
    match = re.match(r"^(===|==|!=|~=|>=|<=|>|<)\s*([0-9A-Za-z][0-9A-Za-z._-]*)", spec.strip())
    if not match:
        return None
    return match.group(1), match.group(2)


def _version_tuple(version: str) -> Tuple[int, ...]:
    parts = []
    for chunk in re.split(r"[._-]", version):
        if chunk.isdigit():
            parts.append(int(chunk))
        else:
            break
    return tuple(parts) or (0,)


def _constraints_conflict(a: str, b: str) -> bool:
    """Conservative conflict check for simple numeric pins (e.g. ==1 vs ==2, >=3 vs <3)."""
    ca = _parse_version_constraint(a)
    cb = _parse_version_constraint(b)
    if not ca or not cb:
        return False
    op_a, ver_a = ca
    op_b, ver_b = cb
    va, vb = _version_tuple(ver_a), _version_tuple(ver_b)
    if op_a in {"==", "==="} and op_b in {"==", "==="}:
        return va != vb
    pairs = [(op_a, va, op_b, vb), (op_b, vb, op_a, va)]
    for op_x, vx, op_y, vy in pairs:
        if op_x in {">=", ">"} and op_y in {"==", "==="} and (
            vy < vx or (op_x == ">" and vy <= vx)
        ):
            return True
        if op_x in {"<=", "<"} and op_y in {"==", "==="} and (
            vy > vx or (op_x == "<" and vy >= vx)
        ):
            return True
        if op_x in {">=", ">"} and op_y in {"<=", "<"}:
            if vx > vy or (vx == vy and (op_x == ">" or op_y == "<")):
                return True
    return False


def validate_dependencies(root: str) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    pyproject = os.path.join(root, "pyproject.toml")
    requirements = os.path.join(root, "requirements.txt")
    has_pyproject = os.path.isfile(pyproject)
    has_requirements = os.path.isfile(requirements)

    if not has_pyproject and not has_requirements:
        issues.append(ValidationIssue(
            "error",
            "Neither pyproject.toml nor requirements.txt was found.",
            "Create one of these files and declare a `nitrostack` dependency.",
        ))
        return issues

    py_deps = _parse_pyproject_dependencies(_read(pyproject)) if has_pyproject else []
    req_deps = _parse_requirements(requirements) if has_requirements else []

    if has_pyproject and not py_deps:
        issues.append(ValidationIssue(
            "warning",
            "[project].dependencies is missing or empty in pyproject.toml.",
            "Add your runtime packages, including nitrostack, to [project].dependencies.",
            "pyproject.toml",
        ))
    if has_requirements and not req_deps:
        issues.append(ValidationIssue(
            "warning",
            "requirements.txt is empty.",
            "List runtime packages (at least `nitrostack`) so install/pack can reproduce the environment.",
            "requirements.txt",
        ))

    py_map = dict(_split_req(dep) for dep in py_deps)
    req_map = dict(_split_req(dep) for dep in req_deps)

    if has_pyproject and has_requirements:
        for name in sorted(set(py_map) & set(req_map)):
            if _constraints_conflict(py_map[name], req_map[name]):
                issues.append(ValidationIssue(
                    "error",
                    f"Conflicting version specs for '{name}': "
                    f"pyproject.toml has '{name}{py_map[name] or ''}' but "
                    f"requirements.txt has '{name}{req_map[name] or ''}'.",
                    "Make the two files agree on one version range, then re-run validate.",
                ))

    declared = {_split_req(d)[0] for d in py_deps + req_deps}
    project_name = ""
    if has_pyproject:
        match = re.search(r'^name\s*=\s*["\']([^"\']+)["\']', _read(pyproject), re.MULTILINE)
        project_name = (match.group(1) if match else "").lower()
    if "nitrostack" not in declared and project_name != "nitrostack":
        issues.append(ValidationIssue(
            "error",
            "The `nitrostack` package is not declared in pyproject.toml or requirements.txt.",
            "Add `nitrostack` to [project].dependencies (or requirements.txt) so the server can be installed.",
        ))

    return issues


def _decorator_names(node: ast.AST) -> Set[str]:
    names: Set[str] = set()
    if isinstance(node, ast.Name):
        names.add(node.id)
    elif isinstance(node, ast.Attribute):
        names.add(node.attr)
    elif isinstance(node, ast.Call):
        names.update(_decorator_names(node.func))
    return names


def _module_name_from_path(root: str, path: str) -> str:
    rel = os.path.relpath(path, root)
    no_ext = os.path.splitext(rel)[0]
    parts = no_ext.replace("\\", "/").split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _load_module(path: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create an import spec for '{path}'.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _file_has_decorator(path: str, decorator: str) -> bool:
    try:
        tree = ast.parse(_read(path), filename=path)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for dec in node.decorator_list:
                if decorator in _decorator_names(dec):
                    return True
    return False


def validate_mcp_app_imports(root: str) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    found = False
    for path in _iter_python_files(root):
        if not _file_has_decorator(path, "mcp_app"):
            continue
        found = True
        rel = os.path.relpath(path, root)
        module_name = f"nitrostack_validate_{_module_name_from_path(root, path).replace('.', '_')}"
        try:
            module = _load_module(path, module_name)
        except Exception as exc:
            tb = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            issues.append(ValidationIssue(
                "error",
                f"Failed to import @mcp_app module '{rel}': {tb}",
                "Fix the import/name error in this file (missing package, typo, or circular import) "
                "and re-run `nitrostack-py validate`.",
                rel,
            ))
            continue
        app_classes = [
            obj for obj in vars(module).values()
            if isinstance(obj, type) and hasattr(obj, "_mcp_app_module")
        ]
        if not app_classes:
            issues.append(ValidationIssue(
                "error",
                f"'{rel}' uses @mcp_app but no decorated application class was found after import.",
                "Ensure the @mcp_app decorator is applied to a class in this file.",
                rel,
            ))
            continue
        for cls in app_classes:
            app_module = getattr(cls, "_mcp_app_module", None)
            if app_module is None:
                issues.append(ValidationIssue(
                    "error",
                    f"{cls.__name__} is decorated with @mcp_app but has no root module.",
                    "Pass a real module class to @mcp_app(module=..., server=...).",
                    rel,
                ))
            elif not isinstance(app_module, type):
                issues.append(ValidationIssue(
                    "error",
                    f"{cls.__name__} @mcp_app(module=...) does not reference a class "
                    f"(got {type(app_module).__name__}).",
                    "Pass the AppModule class itself, not an instance or string.",
                    rel,
                ))
    if not found:
        issues.append(ValidationIssue(
            "warning",
            "No @mcp_app-decorated class was found in this project.",
            "If this is an MCP server, decorate your application class with "
            "@mcp_app(module=AppModule, server=ServerConfig(...)).",
        ))
    return issues


def _is_real_class(value: Any) -> bool:
    return isinstance(value, type)


def validate_module_references(root: str) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    for path in _iter_python_files(root):
        if not _file_has_decorator(path, "module"):
            continue
        rel = os.path.relpath(path, root)
        module_name = f"nitrostack_validate_mod_{_module_name_from_path(root, path).replace('.', '_')}"
        try:
            module = _load_module(path, module_name)
        except Exception as exc:
            tb = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            issues.append(ValidationIssue(
                "error",
                f"Failed to import @module file '{rel}': {tb}",
                "Resolve the import error (typo, missing file, or broken relative import) and retry.",
                rel,
            ))
            continue

        for obj in vars(module).values():
            if not isinstance(obj, type) or not hasattr(obj, "_mcp_module_config"):
                continue
            config = getattr(obj, "_mcp_module_config")
            for field in ("imports", "exports", "controllers", "providers"):
                entries = getattr(config, field, []) or []
                for index, entry in enumerate(entries):
                    if not _is_real_class(entry):
                        issues.append(ValidationIssue(
                            "error",
                            f"{obj.__name__}.{field}[{index}] is {entry!r} "
                            f"({type(entry).__name__}), not a class.",
                            f"Use the class object (e.g. {field[:-1].rstrip('e').title()}Class), "
                            "not a string or instance. Check for typos in the @module() lists.",
                            rel,
                        ))
                        continue
                    if field == "imports" and not hasattr(entry, "_mcp_module_config"):
                        issues.append(ValidationIssue(
                            "error",
                            f"{obj.__name__}.imports[{index}] references {entry.__name__}, "
                            "which is not decorated with @module().",
                            "Import a real NitroStack module class, or remove this entry from imports=[].",
                            rel,
                        ))
    return issues


def validate_project(root: Optional[str] = None) -> List[ValidationIssue]:
    root = os.path.abspath(root or os.getcwd())
    if root not in sys.path:
        sys.path.insert(0, root)
    issues: List[ValidationIssue] = []
    issues.extend(validate_dependencies(root))
    issues.extend(validate_mcp_app_imports(root))
    issues.extend(validate_module_references(root))
    return issues


def format_report(issues: Sequence[ValidationIssue]) -> str:
    if not issues:
        return "Validation passed. No issues found."
    lines = ["Validation found the following issues:", ""]
    for issue in issues:
        lines.append(issue.format())
    errors = sum(1 for i in issues if i.severity == "error")
    warnings = sum(1 for i in issues if i.severity == "warning")
    lines.append("")
    lines.append(f"{errors} error(s), {warnings} warning(s).")
    return "\n".join(lines)
