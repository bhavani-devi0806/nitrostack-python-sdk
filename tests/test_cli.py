"""CLI tests: init/ports (develop) plus generate/pack/upgrade/validate."""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack.cli.main import (
    DEFAULT_MCP_PORT,
    DEFAULT_PROJECT_NAME,
    DEFAULT_WIDGETS_PORT,
    OFFICIAL_TEMPLATES,
    _add_port_flags,
    _find_npm,
    _normalize_port,
    _popen_npm,
    _prompt,
    _prompt_template,
    _prompt_yes_no,
    _read_project_env,
    _resolve_runtime_ports,
    _resolve_template,
    _run_npm,
    _upsert_env_var,
    generate_module,
    generate_tool,
    get_claude_config_paths,
    init_project,
    main,
    print_banner,
    register_server,
    run_dev,
    run_start,
)
from nitrostack.core.app import DEFAULT_HTTP_PORT, resolve_http_port
from nitrostack import ExecutionContext
from nitrostack.cli.generate import generate_component, generate_module as generate_module_from_template
from nitrostack.cli.pack import _is_valid_wheel, pack_project
from nitrostack.cli.upgrade import upgrade_project
from nitrostack.cli.validators import validate_project

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPO_ROOT = ROOT


def _cli_env():
    env = os.environ.copy()
    env["PYTHONPATH"] = ROOT
    return env


def test_init_name_is_optional_in_help():
    result = subprocess.run(
        [sys.executable, "-m", "nitrostack.cli.main", "init", "--help"],
        capture_output=True,
        text=True,
        env=_cli_env(),
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    help_text = result.stdout
    assert "prompted if omitted" in help_text
    assert "[name]" in help_text
    assert "python-starter" in help_text
    assert "python-pizzaz" in help_text
    assert "python-oauth" in help_text
    print("Success! init name is optional and templates are explicit.")


def test_init_rejects_legacy_template_names():
    result = subprocess.run(
        [sys.executable, "-m", "nitrostack.cli.main", "init", "demo", "--template", "starter"],
        capture_output=True,
        text=True,
        env=_cli_env(),
        cwd=ROOT,
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "python-starter" in combined or "invalid choice" in combined.lower() or "unrecognized" in combined.lower() or "invalid" in combined.lower()
    try:
        _resolve_template("starter")
        raise AssertionError("legacy template alias should be rejected")
    except SystemExit:
        pass
    print("Success! Legacy template aliases are rejected.")


def test_init_with_name_and_explicit_template():
    tmp = tempfile.mkdtemp(prefix="nitro-cli-")
    original_cwd = os.getcwd()
    original_stdin = sys.stdin
    try:
        os.chdir(tmp)
        sys.stdin = io.StringIO("A test server\nTester\n")
        init_project("cli-demo", template="python-starter", skip_install=True)
        project = os.path.join(tmp, "cli-demo")
        assert os.path.isfile(os.path.join(project, "main.py"))
        env_text = open(os.path.join(project, ".env"), encoding="utf-8").read()
        assert "PORT=3000" in env_text
        assert "WIDGETS_DEV_PORT=3001" in env_text
        assert "SERVER_DESC=\"A test server\"" in env_text
        assert "SERVER_AUTHOR=\"Tester\"" in env_text
        print("Success! Named init with python-starter writes PORT=3000.")
    finally:
        sys.stdin = original_stdin
        os.chdir(original_cwd)
        shutil.rmtree(tmp, ignore_errors=True)


def test_init_prompts_for_name_when_omitted():
    tmp = tempfile.mkdtemp(prefix="nitro-cli-name-")
    original_cwd = os.getcwd()
    original_stdin = sys.stdin
    try:
        os.chdir(tmp)
        sys.stdin = io.StringIO("prompted-server\n\n\n")
        init_project(None, template="python-starter", skip_install=True)
        assert os.path.isdir(os.path.join(tmp, "prompted-server"))
        print("Success! Missing name is asked on the next readline.")
    finally:
        sys.stdin = original_stdin
        os.chdir(original_cwd)
        shutil.rmtree(tmp, ignore_errors=True)


def test_init_default_name_when_blank_readline():
    tmp = tempfile.mkdtemp(prefix="nitro-cli-default-")
    original_cwd = os.getcwd()
    original_stdin = sys.stdin
    try:
        os.chdir(tmp)
        sys.stdin = io.StringIO("\n\n\n")
        init_project(None, template="python-pizzaz", skip_install=True)
        assert os.path.isdir(os.path.join(tmp, DEFAULT_PROJECT_NAME))
        env_text = open(os.path.join(tmp, DEFAULT_PROJECT_NAME, ".env"), encoding="utf-8").read()
        assert "PORT=3000" in env_text
        print("Success! Blank name readline falls back to my-mcp-server.")
    finally:
        sys.stdin = original_stdin
        os.chdir(original_cwd)
        shutil.rmtree(tmp, ignore_errors=True)


def test_init_python_oauth_template():
    tmp = tempfile.mkdtemp(prefix="nitro-cli-oauth-")
    original_cwd = os.getcwd()
    original_stdin = sys.stdin
    try:
        os.chdir(tmp)
        sys.stdin = io.StringIO("\n\n")
        init_project("oauth-demo", template="python-oauth", skip_install=True)
        project = os.path.join(tmp, "oauth-demo")
        assert os.path.isfile(os.path.join(project, "OAUTH_SETUP.md"))
        env_text = open(os.path.join(project, ".env"), encoding="utf-8").read()
        assert "PORT=3000" in env_text
        print("Success! python-oauth template scaffolds with PORT=3000.")
    finally:
        sys.stdin = original_stdin
        os.chdir(original_cwd)
        shutil.rmtree(tmp, ignore_errors=True)


def test_port_reservation_helpers():
    old_port = os.environ.pop("PORT", None)
    old_mcp = os.environ.pop("MCP_SERVER_PORT", None)
    try:
        # `_resolve_runtime_ports` is the single authority on port precedence.
        assert _resolve_runtime_ports(None, None) == (DEFAULT_MCP_PORT, DEFAULT_WIDGETS_PORT)
        os.environ["PORT"] = "8080"
        assert _resolve_runtime_ports(None, None)[0] == "8080"
        assert _resolve_runtime_ports("9090", "9091") == ("9090", "9091")
        print("Success! 3000 is MCP, 3001 is reserved for widgets.")
    finally:
        if old_port is not None:
            os.environ["PORT"] = old_port
        else:
            os.environ.pop("PORT", None)
        if old_mcp is not None:
            os.environ["MCP_SERVER_PORT"] = old_mcp
        else:
            os.environ.pop("MCP_SERVER_PORT", None)


def test_resolve_http_port_defaults_to_3000():
    old_port = os.environ.pop("PORT", None)
    old_mcp = os.environ.pop("MCP_SERVER_PORT", None)
    try:
        assert DEFAULT_HTTP_PORT == 3000
        assert resolve_http_port() == 3000
        os.environ["PORT"] = "4000"
        assert resolve_http_port() == 4000
        print("Success! HTTP/dual default port is 3000.")
    finally:
        if old_port is not None:
            os.environ["PORT"] = old_port
        else:
            os.environ.pop("PORT", None)
        if old_mcp is not None:
            os.environ["MCP_SERVER_PORT"] = old_mcp
        else:
            os.environ.pop("MCP_SERVER_PORT", None)


def test_cli_dev_and_start_port_flags():
    for command in ("init", "dev", "start"):
        help_result = subprocess.run(
            [sys.executable, "-m", "nitrostack.cli.main", command, "--help"],
            capture_output=True,
            text=True,
            env=_cli_env(),
            cwd=ROOT,
        )
        assert help_result.returncode == 0, help_result.stderr
        assert "--port" in help_result.stdout
        assert "--widget" in help_result.stdout
        assert "3000" in help_result.stdout
        assert "3001" in help_result.stdout
    print("Success! init, dev, and start expose optional --port and --widget flags.")


def test_init_port_and_widget_flags_override_defaults():
    import json

    tmp = tempfile.mkdtemp(prefix="nitro-cli-ports-")
    original_cwd = os.getcwd()
    original_stdin = sys.stdin
    try:
        os.chdir(tmp)
        sys.stdin = io.StringIO("\n\n")
        init_project(
            "ports-demo",
            template="python-starter",
            skip_install=True,
            port="4000",
            widget="4001",
        )
        env_text = open(os.path.join(tmp, "ports-demo", ".env"), encoding="utf-8").read()
        assert "PORT=4000" in env_text
        assert "WIDGETS_DEV_PORT=4001" in env_text
        # The port is supplied once by the CLI at run time, not baked into the
        # npm scripts, so the scripts stay port-free.
        pkg = json.load(open(os.path.join(tmp, "ports-demo", "src", "widgets", "package.json"), encoding="utf-8"))
        assert pkg["scripts"]["dev"] == "next dev"
        assert pkg["scripts"]["start"] == "next start"
        print("Success! --port and --widget override generated project ports.")
    finally:
        sys.stdin = original_stdin
        os.chdir(original_cwd)
        shutil.rmtree(tmp, ignore_errors=True)


def test_init_install_dependencies_prompt_no():
    tmp = tempfile.mkdtemp(prefix="nitro-cli-install-")
    original_cwd = os.getcwd()
    original_stdin = sys.stdin
    try:
        os.chdir(tmp)
        sys.stdin = io.StringIO("desc\nauthor\nn\n")
        init_project("no-npm", template="python-starter")
        widgets = os.path.join(tmp, "no-npm", "src", "widgets", "node_modules")
        assert not os.path.isdir(widgets)
        print("Success! Install dependencies (Y/n) respects n.")
    finally:
        sys.stdin = original_stdin
        os.chdir(original_cwd)
        shutil.rmtree(tmp, ignore_errors=True)


def test_resolve_runtime_ports_flags_override_reservation():
    mcp, widget = _resolve_runtime_ports(port="4000", widget="4001")
    assert mcp == "4000"
    assert widget == "4001"
    mcp, widget = _resolve_runtime_ports(port="3001", widget="3000")
    assert mcp == "3001"
    assert widget == "3000"

    tmp = tempfile.mkdtemp(prefix="nitro-cli-resolve-")
    original_cwd = os.getcwd()
    old_port = os.environ.pop("PORT", None)
    old_mcp = os.environ.pop("MCP_SERVER_PORT", None)
    old_widget = os.environ.pop("WIDGETS_DEV_PORT", None)
    try:
        os.chdir(tmp)
        mcp, widget = _resolve_runtime_ports()
        assert mcp == DEFAULT_MCP_PORT
        assert widget == DEFAULT_WIDGETS_PORT
        print("Success! Explicit --port/--widget override 3000/3001 reservation.")
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(tmp, ignore_errors=True)
        if old_port is not None:
            os.environ["PORT"] = old_port
        if old_mcp is not None:
            os.environ["MCP_SERVER_PORT"] = old_mcp
        if old_widget is not None:
            os.environ["WIDGETS_DEV_PORT"] = old_widget


def test_run_npm_does_not_use_shell():
    from unittest.mock import patch

    with patch("nitrostack.cli.main.shutil.which", return_value="/usr/local/bin/npm"), patch(
        "nitrostack.cli.main.subprocess.run"
    ) as run:
        _run_npm(["--version"], capture_output=True, check=True)
        run.assert_called_once()
        args, kwargs = run.call_args
        assert args[0] == ["/usr/local/bin/npm", "--version"]
        assert kwargs.get("shell") in (None, False)
    print("Success! npm is invoked as an argv list without shell=True.")


def test_prompt_uses_value_or_default():
    original = sys.stdin
    try:
        sys.stdin = io.StringIO("custom\n")
        assert _prompt("Name", "fallback") == "custom"
        sys.stdin = io.StringIO("\n")
        assert _prompt("Name", "fallback") == "fallback"

        class Boom:
            def readline(self):
                raise OSError("closed")

        sys.stdin = Boom()
        assert _prompt("Name", "fallback") == "fallback"
        print("Success! _prompt returns input, default, or fallback on error.")
    finally:
        sys.stdin = original


def test_resolve_template_accepts_official_names():
    for name in OFFICIAL_TEMPLATES:
        assert _resolve_template(name) == name
        assert _resolve_template(name.upper()) == name
    print("Success! _resolve_template accepts official names case-insensitively.")


def test_prompt_template_choice_and_explicit_name():
    original = sys.stdin
    try:
        sys.stdin = io.StringIO("2\n")
        assert _prompt_template() == "python-pizzaz"
        sys.stdin = io.StringIO("python-oauth\n")
        assert _prompt_template() == "python-oauth"
        sys.stdin = io.StringIO("nope\n3\n")
        assert _prompt_template() == "python-oauth"
        sys.stdin = io.StringIO("\n")
        assert _prompt_template() == "python-starter"
        print("Success! _prompt_template handles numbers, names, retries, and default.")
    finally:
        sys.stdin = original


def test_normalize_port_valid_and_invalid():
    assert _normalize_port("4000", "--port") == "4000"
    assert _normalize_port(3001, "--widget") == "3001"
    try:
        _normalize_port("abc", "--port")
        raise AssertionError("non-numeric port should exit")
    except SystemExit:
        pass
    try:
        _normalize_port("99999", "--port")
        raise AssertionError("out-of-range port should exit")
    except SystemExit:
        pass
    try:
        _normalize_port("0", "--widget")
        raise AssertionError("port 0 should exit")
    except SystemExit:
        pass
    print("Success! _normalize_port validates numeric range.")


def test_read_project_env_parses_file():
    tmp = tempfile.mkdtemp(prefix="nitro-cli-env-")
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp)
        assert _read_project_env() == {}
        with open(".env", "w", encoding="utf-8") as f:
            f.write('PORT=4000\n# comment\nWIDGETS_DEV_PORT="4001"\nEMPTY=\n')
        values = _read_project_env()
        assert values["PORT"] == "4000"
        assert values["WIDGETS_DEV_PORT"] == "4001"
        print("Success! _read_project_env parses .env keys and quoted values.")
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(tmp, ignore_errors=True)


def test_upsert_env_var_replaces_and_appends():
    lines = ["PORT=3000\n", "NODE_ENV=development\n"]
    updated = _upsert_env_var(lines, "PORT", "4000")
    assert "PORT=4000\n" in updated
    assert sum(1 for line in updated if line.startswith("PORT=")) == 1
    appended = _upsert_env_var(updated, "WIDGETS_DEV_PORT", "4001")
    assert "WIDGETS_DEV_PORT=4001\n" in appended
    print("Success! _upsert_env_var replaces existing keys and appends missing ones.")


def test_prompt_yes_no_answers():
    original = sys.stdin
    try:
        sys.stdin = io.StringIO("\n")
        assert _prompt_yes_no("Install", default_yes=True) is True
        sys.stdin = io.StringIO("\n")
        assert _prompt_yes_no("Install", default_yes=False) is False
        sys.stdin = io.StringIO("y\n")
        assert _prompt_yes_no("Install", default_yes=False) is True
        sys.stdin = io.StringIO("yes\n")
        assert _prompt_yes_no("Install") is True
        sys.stdin = io.StringIO("n\n")
        assert _prompt_yes_no("Install") is False
        sys.stdin = io.StringIO("no\n")
        assert _prompt_yes_no("Install") is False
        sys.stdin = io.StringIO("maybe\n")
        assert _prompt_yes_no("Install", default_yes=True) is True
        print("Success! _prompt_yes_no handles Y/n, empty default, and unknown input.")
    finally:
        sys.stdin = original


def test_find_npm_found_and_missing():
    from unittest.mock import patch

    with patch("nitrostack.cli.main.shutil.which", return_value="/usr/bin/npm"):
        assert _find_npm() == "/usr/bin/npm"
    with patch("nitrostack.cli.main.shutil.which", return_value=None):
        try:
            _find_npm()
            raise AssertionError("missing npm should raise FileNotFoundError")
        except FileNotFoundError as exc:
            assert "npm was not found" in str(exc)
    print("Success! _find_npm returns the binary or raises FileNotFoundError.")


def test_popen_npm_does_not_use_shell():
    from unittest.mock import patch

    with patch("nitrostack.cli.main.shutil.which", return_value="/usr/local/bin/npm"), patch(
        "nitrostack.cli.main.subprocess.Popen"
    ) as popen:
        _popen_npm(["run", "dev", "--", "--port", "3001"], cwd=".")
        popen.assert_called_once()
        args, kwargs = popen.call_args
        assert args[0] == ["/usr/local/bin/npm", "run", "dev", "--", "--port", "3001"]
        assert kwargs.get("shell") in (None, False)
    print("Success! _popen_npm uses an argv list without shell=True.")


def test_add_port_flags_registers_optional_overrides():
    import argparse

    parser = argparse.ArgumentParser()
    _add_port_flags(parser)
    args = parser.parse_args([])
    assert args.port is None
    assert args.widget is None
    args = parser.parse_args(["--port", "4000", "--widget", "4001"])
    assert args.port == "4000"
    assert args.widget == "4001"
    print("Success! _add_port_flags registers optional --port and --widget.")


def test_print_banner_includes_brand():
    original = sys.stdout
    try:
        buf = io.StringIO()
        sys.stdout = buf
        print_banner()
        assert "NITROSTACK" in buf.getvalue()
        print("Success! print_banner prints the NitroStack banner.", file=original)
    finally:
        sys.stdout = original


def test_init_project_rejects_blank_name_and_overwrite_cancel():
    tmp = tempfile.mkdtemp(prefix="nitro-cli-edge-")
    original_cwd = os.getcwd()
    original_stdin = sys.stdin
    try:
        os.chdir(tmp)
        try:
            init_project("   ", template="python-starter", skip_install=True)
            raise AssertionError("blank name should exit")
        except SystemExit:
            pass
        os.mkdir("taken")
        sys.stdin = io.StringIO("n\n")
        try:
            init_project("taken", template="python-starter", skip_install=True)
            raise AssertionError("overwrite cancel should exit")
        except SystemExit as exc:
            assert exc.code == 0
        assert os.path.isdir("taken")
        print("Success! init_project rejects blank names and cancelled overwrite.")
    finally:
        sys.stdin = original_stdin
        os.chdir(original_cwd)
        shutil.rmtree(tmp, ignore_errors=True)


def test_init_project_overwrite_and_install_yes_calls_npm():
    from unittest.mock import patch

    tmp = tempfile.mkdtemp(prefix="nitro-cli-ow-")
    original_cwd = os.getcwd()
    original_stdin = sys.stdin
    try:
        os.chdir(tmp)
        os.mkdir("taken")
        open(os.path.join("taken", "old.txt"), "w", encoding="utf-8").write("x")
        sys.stdin = io.StringIO("y\ndesc\nauthor\n")
        with patch("nitrostack.cli.main._run_npm") as npm:
            init_project("taken", template="python-starter", skip_install=True)
            npm.assert_not_called()
        assert os.path.isfile(os.path.join("taken", "main.py"))
        assert not os.path.exists(os.path.join("taken", "old.txt"))

        sys.stdin = io.StringIO("d\na\nY\n")
        with patch("nitrostack.cli.main._run_npm") as npm:
            init_project("installed", template="python-starter")
            assert npm.call_count >= 2
            called_args = [call.args[0] for call in npm.call_args_list]
            assert ["--version"] in called_args
            assert ["install"] in called_args
        print("Success! init_project overwrite and install-yes npm path work.")
    finally:
        sys.stdin = original_stdin
        os.chdir(original_cwd)
        shutil.rmtree(tmp, ignore_errors=True)


def test_run_dev_and_start_missing_main_exit():
    tmp = tempfile.mkdtemp(prefix="nitro-cli-missing-")
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp)
        try:
            run_dev()
            raise AssertionError("run_dev without main.py should exit")
        except SystemExit:
            pass
        try:
            run_start()
            raise AssertionError("run_start without main.py should exit")
        except SystemExit:
            pass
        print("Success! run_dev and run_start exit when main.py is missing.")
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(tmp, ignore_errors=True)


def test_run_dev_passes_port_overrides_and_starts_widgets():
    from types import ModuleType
    from unittest.mock import MagicMock, patch

    tmp = tempfile.mkdtemp(prefix="nitro-cli-dev-")
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp)
        open("main.py", "w", encoding="utf-8").write("print('ok')\n")
        widgets = os.path.join("src", "widgets")
        os.makedirs(widgets)
        open(os.path.join(widgets, "package.json"), "w", encoding="utf-8").write("{}")

        fake_watchfiles = ModuleType("watchfiles")

        def _boom(*_a, **_k):
            raise KeyboardInterrupt()

        fake_watchfiles.watch = _boom
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None

        with patch.dict(sys.modules, {"watchfiles": fake_watchfiles}), patch(
            "nitrostack.cli.main.subprocess.Popen", return_value=fake_proc
        ) as popen, patch("nitrostack.cli.main._popen_npm") as npm:
            run_dev(port="4000", widget="4001")
            npm.assert_called_once()
            assert npm.call_args.args[0] == ["run", "dev", "--", "--port", "4001"]
            env = popen.call_args.kwargs["env"]
            assert env["PORT"] == "4000"
            assert env["WIDGETS_DEV_PORT"] == "4001"
        print("Success! run_dev applies --port/--widget and starts widgets.")
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(tmp, ignore_errors=True)


def test_run_start_passes_port_overrides():
    from unittest.mock import patch

    tmp = tempfile.mkdtemp(prefix="nitro-cli-start-")
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp)
        open("main.py", "w", encoding="utf-8").write("print('ok')\n")
        widgets = os.path.join("src", "widgets")
        os.makedirs(widgets)
        open(os.path.join(widgets, "package.json"), "w", encoding="utf-8").write("{}")
        # `next start` needs a production build; run_start skips the widget
        # server without one, so create it for this pass-through check.
        os.makedirs(os.path.join(widgets, ".next"))
        with patch("nitrostack.cli.main.subprocess.run") as run, patch("nitrostack.cli.main._popen_npm") as npm:
            run_start(port="4000", widget="4001")
            npm.assert_called_once()
            assert npm.call_args.args[0] == ["run", "start", "--", "--port", "4001"]
            env = run.call_args.kwargs["env"]
            assert env["PORT"] == "4000"
            assert env["WIDGETS_DEV_PORT"] == "4001"
            assert env["NODE_ENV"] == "production"
            # `start` must pick HTTP explicitly: dual tears HTTP down on STDIO EOF.
            assert env["MCP_TRANSPORT_TYPE"] == "http"
        print("Success! run_start applies --port/--widget and production env.")
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(tmp, ignore_errors=True)


def test_generate_tool_and_module():
    tmp = tempfile.mkdtemp(prefix="nitro-cli-gen-")
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp)
        generate_tool("hello_world")
        assert os.path.isfile("hello_world_tool.py")
        content = open("hello_world_tool.py", encoding="utf-8").read()
        assert "hello_world" in content
        assert "HelloWorldInput" in content
        try:
            generate_tool("hello_world")
            raise AssertionError("duplicate generate_tool should exit")
        except SystemExit:
            pass
        generate_module("billing")
        assert os.path.isfile("billing_module.py")
        module_src = open("billing_module.py", encoding="utf-8").read()
        assert "class BillingModule" in module_src
        try:
            generate_module("billing")
            raise AssertionError("duplicate generate_module should exit")
        except SystemExit:
            pass
        print("Success! generate_tool and generate_module write files and refuse overwrites.")
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(tmp, ignore_errors=True)


def test_get_claude_config_paths_by_platform():
    from unittest.mock import patch

    with patch("nitrostack.cli.main.sys.platform", "darwin"), patch(
        "nitrostack.cli.main.os.path.exists", return_value=True
    ):
        paths = get_claude_config_paths()
        assert paths
        assert any("Application Support" in p and "Claude" in p for p in paths)

    with patch("nitrostack.cli.main.sys.platform", "linux"), patch(
        "nitrostack.cli.main.os.path.exists", return_value=True
    ):
        paths = get_claude_config_paths()
        assert any(".config" in p and "Claude" in p for p in paths)

    with patch("nitrostack.cli.main.sys.platform", "win32"), patch.dict(
        os.environ, {"APPDATA": "/appdata"}, clear=False
    ), patch("nitrostack.cli.main.os.environ.get", side_effect=lambda k, d=None: "/appdata" if k == "APPDATA" else None), patch(
        "nitrostack.cli.main.os.path.exists", return_value=True
    ):
        paths = get_claude_config_paths()
        assert any("claude_desktop_config.json" in p for p in paths)
    print("Success! get_claude_config_paths returns platform-specific Claude config paths.")


def test_register_server_writes_config_and_handles_errors():
    from unittest.mock import patch

    tmp = tempfile.mkdtemp(prefix="nitro-cli-reg-")
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp)
        try:
            register_server("demo", "missing.py")
            raise AssertionError("missing script should exit")
        except SystemExit:
            pass
        open("server.py", "w", encoding="utf-8").write("print('hi')\n")
        with patch("nitrostack.cli.main.get_claude_config_paths", return_value=[]):
            try:
                register_server("demo", "server.py")
                raise AssertionError("missing Claude config should exit")
            except SystemExit:
                pass
        config_path = os.path.join(tmp, "Claude", "claude_desktop_config.json")
        os.makedirs(os.path.dirname(config_path))
        with patch("nitrostack.cli.main.get_claude_config_paths", return_value=[config_path]):
            register_server("demo", "server.py")
        import json

        config = json.load(open(config_path, encoding="utf-8"))
        assert "demo" in config["mcpServers"]
        assert config["mcpServers"]["demo"]["args"][0].endswith("server.py")
        print("Success! register_server writes Claude config and exits on errors.")
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(tmp, ignore_errors=True)


def test_main_dispatches_commands():
    from unittest.mock import patch

    with patch("sys.argv", ["nitrostack-py"]), patch("nitrostack.cli.main.argparse.ArgumentParser.print_help"):
        try:
            main()
            raise AssertionError("main with no command should exit")
        except SystemExit as exc:
            assert exc.code == 1

    with patch("sys.argv", ["nitrostack-py", "init", "demo", "--template", "python-starter", "--skip-install"]), patch(
        "nitrostack.cli.main.init_project"
    ) as init:
        main()
        init.assert_called_once()
        kwargs = init.call_args.kwargs
        assert init.call_args.args[0] == "demo"
        assert kwargs["skip_install"] is True

    with patch("sys.argv", ["nitrostack-py", "dev", "--port", "4000", "--widget", "4001"]), patch(
        "nitrostack.cli.main.run_dev"
    ) as dev:
        main()
        dev.assert_called_once_with(port="4000", widget="4001")

    with patch("sys.argv", ["nitrostack-py", "start", "--port", "4000", "--widget", "4001"]), patch(
        "nitrostack.cli.main.run_start"
    ) as start:
        main()
        start.assert_called_once_with(port="4000", widget="4001")

    with patch("sys.argv", ["nitrostack-py", "generate", "tool", "ping"]), patch(
        "nitrostack.cli.main.generate_tool"
    ) as gen_tool:
        main()
        gen_tool.assert_called_once_with("ping")

    with patch("sys.argv", ["nitrostack-py", "generate", "module", "orders"]), patch(
        "nitrostack.cli.main.generate_module"
    ) as gen_mod:
        main()
        gen_mod.assert_called_once_with("orders")

    with patch("sys.argv", ["nitrostack-py", "register", "--name", "x", "--file", "main.py"]), patch(
        "nitrostack.cli.main.register_server"
    ) as reg:
        main()
        reg.assert_called_once_with("x", "main.py")
    print("Success! main() dispatches init/dev/start/generate/register.")


def test_official_templates_map_to_existing_dirs():
    import nitrostack
    package_dir = os.path.dirname(nitrostack.__file__)
    for official, folder in OFFICIAL_TEMPLATES.items():
        path = os.path.join(package_dir, "templates", folder)
        assert os.path.isdir(path), f"missing template dir for {official}: {path}"
    print("Success! Explicit template names map to on-disk folders.")


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
        generate_module_from_template(name, cwd=str(tmp_path))
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


if __name__ == "__main__":
    test_init_name_is_optional_in_help()
    test_init_rejects_legacy_template_names()
    test_init_with_name_and_explicit_template()
    test_init_prompts_for_name_when_omitted()
    test_init_default_name_when_blank_readline()
    test_init_python_oauth_template()
    test_port_reservation_helpers()
    test_resolve_http_port_defaults_to_3000()
    test_cli_dev_and_start_port_flags()
    test_init_port_and_widget_flags_override_defaults()
    test_init_install_dependencies_prompt_no()
    test_resolve_runtime_ports_flags_override_reservation()
    test_run_npm_does_not_use_shell()
    test_prompt_uses_value_or_default()
    test_resolve_template_accepts_official_names()
    test_prompt_template_choice_and_explicit_name()
    test_normalize_port_valid_and_invalid()
    test_read_project_env_parses_file()
    test_upsert_env_var_replaces_and_appends()
    test_prompt_yes_no_answers()
    test_find_npm_found_and_missing()
    test_popen_npm_does_not_use_shell()
    test_add_port_flags_registers_optional_overrides()
    test_print_banner_includes_brand()
    test_init_project_rejects_blank_name_and_overwrite_cancel()
    test_init_project_overwrite_and_install_yes_calls_npm()
    test_run_dev_and_start_missing_main_exit()
    test_run_dev_passes_port_overrides_and_starts_widgets()
    test_run_start_passes_port_overrides()
    test_generate_tool_and_module()
    test_get_claude_config_paths_by_platform()
    test_register_server_writes_config_and_handles_errors()
    test_main_dispatches_commands()
    test_official_templates_map_to_existing_dirs()
    print("\nAll CLI tests passed.")
