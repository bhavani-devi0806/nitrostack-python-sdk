"""Phase 5 CLI tests: generate, pack, upgrade, validate."""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import io
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

from nitrostack import ExecutionContext
from nitrostack.cli.generate import generate_component, generate_module
from nitrostack.cli.main import main
from nitrostack.cli.pack import _is_valid_wheel, pack_project
from nitrostack.cli.upgrade import upgrade_project
from nitrostack.cli.validators import validate_project


GENERATE_CASES = [
    ("guard", "MyGuard", Path("guards") / "my_guard.py", "MyGuard"),
    ("pipe", "Validation", Path("pipes") / "validation.py", "ValidationPipe"),
    ("interceptor", "Transform", Path("interceptors") / "transform.py", "TransformInterceptor"),
    ("filter", "HttpException", Path("filters") / "http_exception.py", "HttpExceptionFilter"),
    ("service", "Email", Path("services") / "email.py", "EmailService"),
    ("module", "payments", Path("payments_module.py"), "PaymentsModule"),
]


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _invoke_cli(args, cwd: Path) -> tuple[int, str]:
    old_cwd = os.getcwd()
    old_argv = sys.argv
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        os.chdir(cwd)
        sys.argv = ["nitrostack-py", *args]
        try:
            with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                main()
            code = 0
        except SystemExit as exc:
            code = int(exc.code or 0)
    finally:
        os.chdir(old_cwd)
        sys.argv = old_argv
    return code, stdout.getvalue() + stderr.getvalue()


def test_cli_help_lists_new_commands():
    env = os.environ.copy()
    env["PYTHONPATH"] = REPO_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "nitrostack.cli.main", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0
    help_text = result.stdout
    for command in ("init", "dev", "start", "register", "generate", "pack", "upgrade", "install", "validate"):
        assert command in help_text, f"expected {command!r} in --help output"


def test_generate_guard_myguard_importable(tmp_path: Path):
    generate_component("guard", "MyGuard", cwd=str(tmp_path))
    path = tmp_path / "guards" / "my_guard.py"
    assert path.is_file()
    ast.parse(path.read_text(encoding="utf-8"))
    module = _load_module(path, "generated_my_guard")
    instance = module.MyGuard()
    ctx = ExecutionContext(request_id="cli-test")
    assert asyncio.run(instance.can_activate(ctx)) is False


@pytest.mark.parametrize("kind,name,rel_path,class_name", GENERATE_CASES)
def test_generate_targets_valid_python(tmp_path: Path, kind: str, name: str, rel_path: Path, class_name: str):
    if kind == "module":
        generate_module(name, cwd=str(tmp_path))
    else:
        generate_component(kind, name, cwd=str(tmp_path))
    path = tmp_path / rel_path
    assert path.is_file(), f"expected generated file at {rel_path}"
    source = path.read_text(encoding="utf-8")
    ast.parse(source)
    compile(source, str(path), "exec")
    module = _load_module(path, f"generated_{kind}_{class_name}")
    cls = getattr(module, class_name)
    instance = cls()
    assert instance is not None


def _mini_project(tmp_path: Path) -> Path:
    (tmp_path / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "app_module.py").write_text("NAME = 'demo'\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("nitrostack\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("PORT=8000\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=should-never-pack\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("keep me\n", encoding="utf-8")
    return tmp_path


def test_pack_dry_run_lists_files_without_writing(tmp_path: Path):
    project = _mini_project(tmp_path)
    result = pack_project(str(project), dry_run=True)
    assert result["dry_run"] is True
    files = result["files"]
    assert "main.py" in files
    assert "app_module.py" in files
    assert "requirements.txt" in files
    assert ".env.example" in files
    assert ".env" not in files
    assert not list(project.rglob("*.whl"))
    assert not (project / "dist").exists()


def test_pack_creates_valid_wheel(tmp_path: Path):
    project = _mini_project(tmp_path)
    result = pack_project(str(project), dry_run=False)
    wheel = Path(result["wheel"])
    assert wheel.is_file()
    assert wheel.suffix == ".whl"
    assert zipfile.is_zipfile(wheel)
    assert _is_valid_wheel(str(wheel))

    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
        assert any(n.endswith(".dist-info/WHEEL") for n in names)
        assert any(n.endswith(".dist-info/METADATA") for n in names)
        assert any(n.endswith(".dist-info/RECORD") for n in names)
        wheel_entry = next(n for n in names if n.endswith(".dist-info/WHEEL"))
        wheel_body = zf.read(wheel_entry).decode("utf-8")
        assert "Wheel-Version:" in wheel_body
        assert any(n == ".env.example" or n.endswith("/.env.example") for n in names)
        assert not any(n == ".env" or n.endswith("/.env") for n in names)
        assert "SECRET=should-never-pack" not in "\n".join(
            zf.read(n).decode("utf-8", errors="ignore") for n in names if not n.endswith("/")
        )


def test_upgrade_dry_run_does_not_modify_pyproject(tmp_path: Path):
    original = (
        "[project]\n"
        'name = "demo"\n'
        'version = "0.1.0"\n'
        "dependencies = [\n"
        '    "nitrostack>=0.1.0",\n'
        "]\n"
    )
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(original, encoding="utf-8")
    with patch("nitrostack.cli.upgrade.fetch_latest_nitrostack_version", return_value="9.9.9"):
        result = upgrade_project(str(tmp_path), dry_run=True, verify=False)
    assert result["dry_run"] is True
    assert result["version"] == "9.9.9"
    assert pyproject.read_text(encoding="utf-8") == original
    assert any(change["to"] == "nitrostack>=9.9.9" for change in result["changes"])


def test_validate_catches_broken_imports_and_module_refs(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "broken-demo"\n'
        'version = "0.1.0"\n'
        "dependencies = [\n"
        '    "nitrostack==1.0.0",\n'
        "]\n",
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text("nitrostack==2.0.0\n", encoding="utf-8")
    (tmp_path / "broken_app.py").write_text(
        "from nitrostack import mcp_app, module, ServerConfig\n"
        "from definitely_missing_nitrostack_pkg import Missing\n\n"
        "@module(name='root')\n"
        "class RootModule:\n"
        "    pass\n\n"
        "@mcp_app(module=RootModule, server=ServerConfig(name='broken'))\n"
        "class App:\n"
        "    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "bad_module.py").write_text(
        "from nitrostack import module\n\n"
        "@module(name='bad', imports=['CalculatorModule'], exports=['Nope'], controllers=[123])\n"
        "class BadModule:\n"
        "    pass\n",
        encoding="utf-8",
    )

    issues = validate_project(str(tmp_path))
    messages = "\n".join(issue.format() for issue in issues)
    assert any(issue.severity == "error" for issue in issues)
    assert "conflicting version" in messages.lower() or "Conflicting version" in messages
    assert "definitely_missing_nitrostack_pkg" in messages
    assert "not a class" in messages.lower() or "not a class" in messages
    assert "→" in messages or "Fix the import" in messages


def test_generate_guard_via_cli(tmp_path: Path):
    code, output = _invoke_cli(["generate", "guard", "TestGuard"], tmp_path)
    assert code == 0
    assert (tmp_path / "guards" / "test_guard.py").is_file()
    assert "Generated guard" in output
