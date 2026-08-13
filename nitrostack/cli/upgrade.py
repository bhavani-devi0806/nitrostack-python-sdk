"""In-place nitrostack version upgrades for `nitrostack-py upgrade`."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Optional, Tuple

PYPI_JSON = "https://pypi.org/pypi/nitrostack/json"
PYPI_VERSION_JSON = "https://pypi.org/pypi/nitrostack/{version}/json"

_DEP_RE = re.compile(
    r'(["\']?)(nitrostack)((?:\s*(?:===|==|!=|~=|>=|<=|>|<)\s*[^"\'\s,#]+)?)(\1)',
    re.IGNORECASE,
)


class UpgradeError(RuntimeError):
    pass


def fetch_latest_nitrostack_version(timeout: float = 15.0) -> str:
    req = urllib.request.Request(
        PYPI_JSON,
        headers={"Accept": "application/json", "User-Agent": "nitrostack-py"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise UpgradeError(
            f"Could not reach PyPI to determine the latest nitrostack version: {exc}\n"
            "Check your network connection, then retry."
        ) from exc
    version = (payload.get("info") or {}).get("version")
    if not version:
        raise UpgradeError("PyPI response did not include a version for nitrostack.")
    return str(version)


def verify_nitrostack_version(version: str, timeout: float = 15.0) -> None:
    req = urllib.request.Request(
        PYPI_VERSION_JSON.format(version=version),
        headers={"Accept": "application/json", "User-Agent": "nitrostack-py"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if getattr(resp, "status", 200) >= 400:
                raise UpgradeError(f"nitrostack=={version} was not found on PyPI.")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpgradeError(
                f"nitrostack=={version} was not found on PyPI. "
                "Check the version string and retry."
            ) from exc
        raise UpgradeError(f"PyPI lookup for nitrostack=={version} failed: {exc}") from exc
    except urllib.error.URLError as exc:
        raise UpgradeError(f"Could not reach PyPI to verify nitrostack=={version}: {exc}") from exc


def find_current_spec(text: str) -> Optional[str]:
    match = _DEP_RE.search(text)
    if not match:
        return None
    spec = (match.group(2) + (match.group(3) or "")).strip()
    return spec


def replace_nitrostack_spec(text: str, version: str) -> Tuple[str, int]:
    replacement = f"nitrostack>={version}"

    def _sub(match: re.Match) -> str:
        quote = match.group(1) or ""
        return f"{quote}{replacement}{quote}"

    return _DEP_RE.subn(_sub, text)


def _add_to_pyproject_dependencies(text: str, spec: str) -> str:
    match = re.search(r"(^dependencies\s*=\s*\[)(.*?)(\])", text, re.MULTILINE | re.DOTALL)
    if not match:
        # Insert a dependencies array under [project] if possible.
        project = re.search(r"^\[project\][^\[]*", text, re.MULTILINE | re.DOTALL)
        if not project:
            raise UpgradeError(
                "pyproject.toml has no [project] table. Add one (or add nitrostack to "
                "requirements.txt) before running upgrade."
            )
        insert_at = project.end()
        block = f'\ndependencies = [\n    "{spec}",\n]\n'
        return text[:insert_at] + block + text[insert_at:]
    inner = match.group(2).rstrip()
    indent = "    "
    addition = f'\n{indent}"{spec}",\n'
    if inner.strip():
        if not inner.rstrip().endswith(","):
            # keep existing formatting; append comma + new item
            addition = f',\n{indent}"{spec}",\n'
        else:
            addition = f'{indent}"{spec}",\n'
        new_inner = inner + ("" if inner.endswith("\n") else "\n") + addition
    else:
        new_inner = addition
    return text[: match.start(2)] + new_inner + text[match.end(2) :]


def upgrade_project(
    root: Optional[str] = None,
    *,
    version: Optional[str] = None,
    dry_run: bool = False,
    verify: bool = True,
) -> dict:
    """Update the nitrostack dependency spec in pyproject.toml (and requirements.txt if present)."""
    root = os.path.abspath(root or os.getcwd())
    pyproject = os.path.join(root, "pyproject.toml")
    requirements = os.path.join(root, "requirements.txt")

    if not os.path.isfile(pyproject) and not os.path.isfile(requirements):
        raise UpgradeError(
            "No pyproject.toml or requirements.txt found in the current directory.\n"
            "Run this command from a NitroStack project, or create pyproject.toml first."
        )

    target = version or fetch_latest_nitrostack_version()
    if version and verify:
        verify_nitrostack_version(target)

    new_spec = f"nitrostack>={target}"
    changes = []
    original_pyproject = _read(pyproject) if os.path.isfile(pyproject) else None
    original_reqs = _read(requirements) if os.path.isfile(requirements) else None

    if original_pyproject is not None:
        current = find_current_spec(original_pyproject)
        updated, n = replace_nitrostack_spec(original_pyproject, target)
        if n == 0:
            updated = _add_to_pyproject_dependencies(original_pyproject, new_spec)
            current = current or "(missing)"
        changes.append(
            {
                "file": "pyproject.toml",
                "from": current or "(missing)",
                "to": new_spec,
                "text": updated,
            }
        )

    # Phase 5 requires pyproject.toml in-place updates. Also keep requirements.txt
    # in sync when it already pins nitrostack, so install/pack stay consistent.
    if original_reqs is not None and find_current_spec(original_reqs):
        current = find_current_spec(original_reqs)
        updated, n = replace_nitrostack_spec(original_reqs, target)
        if n:
            changes.append(
                {
                    "file": "requirements.txt",
                    "from": current,
                    "to": new_spec,
                    "text": updated,
                }
            )

    if not changes:
        raise UpgradeError(
            "Could not find a nitrostack dependency to update.\n"
            "Add `nitrostack` to [project].dependencies in pyproject.toml and retry."
        )

    print("NITROSTACK — Upgrade" + (" (dry run)" if dry_run else ""))
    print(f"Target version: {target}")
    for change in changes:
        print(f"  {change['file']}: {change['from']} → {change['to']}")

    if dry_run:
        print("\nDry run — no files modified.")
        return {"version": target, "changes": changes, "dry_run": True, "written": []}

    written = []
    for change in changes:
        path = os.path.join(root, change["file"])
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(change["text"])
        written.append(change["file"])
        print(f"Updated {change['file']}")

    print(f"\nUpgrade complete. nitrostack dependency is now {new_spec}.")
    print("Run `nitrostack-py install` to install the new version.")
    return {"version": target, "changes": changes, "dry_run": False, "written": written}


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()
