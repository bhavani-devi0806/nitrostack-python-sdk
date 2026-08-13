# NitroStack Python SDK

A Python-idiomatic port of the **NitroStack** Model Context Protocol (MCP) framework, enabling NestJS-like modular architecture, dependency injection, execution pipelines, background task processing, built-in authentication modules, and a diagnostic testing harness.

---

## Features

- **Nested Modular Architecture**: Group components cleanly with `@module`.
- **Dependency Injection**: Explicit class constructor DI with `DIContainer` and `@injectable(deps=[...])`.
- **Pipeline Interceptors**: Build guards, middleware, interceptors, pipes, and exception filters for tool execution.
- **Asynchronous Background Tasks**: Spawn background workers automatically for long-running tools.
- **Built-in Authentication**: Modules for API Keys, JWT verification, and OAuth 2.1 (featuring Protected Resource Metadata discovery servers).
- **In-Process Testing Harness**: Run unit and integration tests against modules without managing subprocesses or real network transports.
- **CLI Tooling (`nitrostack-py`)**: Scaffold apps (`init`), generate components (`generate`), pack deployable wheels (`pack`), upgrade/install dependencies, validate projects, auto-register servers with Claude (`register`), and run hot-reload development servers (`dev`).

---

## Installation

```bash
pip install nitrostack
```

To install local developer or test dependencies:
```bash
pip install -e .
```

---

## Scaffolding a New Project (Recommended)

You can quickly scaffold a new project template using the interactive CLI tool:

```bash
nitrostack-py init my-server
```

*(Or via Python: `python -m nitrostack.cli.main init my-server`)*

This launches an interactive prompt where you can:
1. **Choose a template**:
   - **Starter**: A simple calculator server.
   - **Advanced**: A food delivery server with items and order status tracking.
   - **OAuth**: A flight booking server demonstrating OAuth 2.1 authentication and guarded routes.
2. **Provide metadata**: Specify a custom description and author name.

Once scaffolded, follow the next steps printed by the CLI to run your server, configure environment variables, and try it out.

---

## CLI (`nitrostack-py`)

The CLI is installed with the SDK (`nitrostack-py`, or `python -m nitrostack.cli.main`). Run `nitrostack-py --help` to list commands.

### Project lifecycle

```bash
nitrostack-py init my-server
nitrostack-py dev          # hot-reload development server
nitrostack-py start        # production server (no reload)
nitrostack-py register --name my-mcp-server --file app.py
```

### Generate components

Existing `tool` and `module` generators are unchanged. Additional generators create pipeline and service stubs that follow the current Python decorator/protocol APIs:

```bash
nitrostack-py generate tool add_numbers
nitrostack-py generate module payments
nitrostack-py generate guard MyGuard
nitrostack-py generate pipe Validation
nitrostack-py generate interceptor Transform
nitrostack-py generate filter HttpException
nitrostack-py generate service Email
```

Generated files:

| Command | Output |
|---|---|
| `generate tool <name>` | `{name}_tool.py` in the current directory |
| `generate module <name>` | `{name}_module.py` in the current directory |
| `generate guard <Name>` | `guards/<name>.py` |
| `generate pipe <Name>` | `pipes/<name>.py` |
| `generate interceptor <Name>` | `interceptors/<name>.py` |
| `generate filter <Name>` | `filters/<name>.py` |
| `generate service <Name>` | `services/<name>.py` |

Attach generated pipeline classes with `@use_guards`, `@use_pipes`, `@use_interceptors`, or `@use_filters`. Register services in a module's `providers` list.

### Pack a deployable wheel

```bash
nitrostack-py pack --dry-run    # list files; does not write an artifact
nitrostack-py pack              # write dist/*.whl
```

`pack` builds a wheel with setuptools (the same backend as this SDK), refreshes `requirements.txt` from `pyproject.toml` when possible, and always includes `.env.example`. The real `.env` file and other secrets are never packed. Temporary build directories are deleted afterwards.

### Upgrade, install, validate

```bash
nitrostack-py upgrade                 # latest nitrostack on PyPI
nitrostack-py upgrade --version 0.3.2 # pin a specific version
nitrostack-py upgrade --dry-run       # print the change; do not edit files

nitrostack-py install                 # install project + development dependencies
nitrostack-py install --production    # skip optional extras and requirements-dev.txt

nitrostack-py validate                # lint deps, @mcp_app imports, and @module() refs
```

`upgrade` updates the `nitrostack` dependency spec in `pyproject.toml` in place (and `requirements.txt` when it already pins nitrostack). `validate` reports missing/conflicting dependencies, `@mcp_app` modules that fail to import, and `@module()` `imports`/`exports` that are not real classes.

---

## NitroStudio Dashboard

NitroStudio is an interactive visual developer dashboard for inspecting, graphing, and testing your MCP servers.

To launch the dashboard, execute:
```bash
nitrostack-studio
```
If your Python scripts directory is not configured in your system `PATH`, you can run it via Python:
```bash
python -m nitrostack.studio
```
This launches the interface in your default web browser, allowing you to traverse directories, visualize your dependency graph, test tool execution forms, chat with a local mock LLM, and inspect RPC logs.

---

## Quick Start

### 1. Write your First Server

Create a file named `app.py`:

```python
import asyncio
from pydantic import BaseModel, Field
from nitrostack import (
    tool,
    resource,
    injectable,
    module,
    mcp_app,
    McpApplicationFactory,
    ServerConfig,
    ExecutionContext,
)

# 1. Input Validation Schema
class AddInput(BaseModel):
    a: float = Field(description="First number")
    b: float = Field(description="Second number")

# 2. Injected Provider Service
@injectable(deps=[])
class CalculatorService:
    def add(self, a: float, b: float) -> float:
        return a + b

# 3. Controller
@injectable(deps=[CalculatorService])
class CalculatorController:
    def __init__(self, service: CalculatorService):
        self.service = service

    @tool(
        name="add",
        description="Add two numbers together",
        input_schema=AddInput
    )
    async def add(self, input: AddInput, context: ExecutionContext) -> float:
        context.logger.info(f"Adding {input.a} and {input.b}")
        return self.service.add(input.a, input.b)

    @resource(
        uri="calc://info",
        name="Calculator Info",
        description="Metadata about this calculator"
    )
    async def get_info(self, context: ExecutionContext) -> str:
        return "Simple Add Calculator v1.0.0"

# 4. Modules
@module(
    name="calculator",
    controllers=[CalculatorController],
    providers=[CalculatorService]
)
class CalculatorModule:
    pass

@module(
    name="app",
    imports=[CalculatorModule]
)
class AppModule:
    pass

# 5. Application Entrypoint
@mcp_app(
    module=AppModule,
    server=ServerConfig(name="math-server", version="1.0.0")
)
class App:
    pass

async def main():
    app = await McpApplicationFactory.create(App)
    await app.start()

if __name__ == "__main__":
    asyncio.run(main())
```

### 2. Configure Environment Variables

The SDK reads standard settings from the environment or `.env` files:

| Environment Variable | Description |
|---|---|
| `PORT` / `MCP_SERVER_PORT` | The port to bind for HTTP/SSE transport (default: `8000`). |
| `MCP_TRANSPORT_TYPE` | Transport selection: `stdio`, `http`, or `dual` (combining stdio + HTTP/SSE). |
| `NODE_ENV` | If set to `production`, defaults to `dual` transport. Otherwise defaults to `stdio`. |
| `MCP_MAX_SESSIONS` | Cap on concurrent Streamable HTTP sessions; new sessions beyond the cap get an HTTP `429`. Unset = unlimited. |
| `MCP_SESSION_TIMEOUT_MS` | Idle timeout (ms) for stateful HTTP sessions; sessions with no activity for this long are terminated automatically. Unset = no timeout. |
| `MCP_GRACEFUL_SHUTDOWN_TIMEOUT_MS` | How long (ms) the HTTP transport waits for in-flight requests to finish when shutting down (default: `10000`). |
| `MCP_STATELESS` | Set to `true` to run the HTTP transport in stateless mode: every request gets a fresh context with no session id and no `initialize` handshake required. |
| `MCP_ALLOWED_HOSTS` / `MCP_ALLOWED_ORIGINS` | Comma-separated allow-lists for DNS-rebinding protection, used only when CORS is disabled. |
| `NITROSTACK_LOG_FILE` | Destination file for logs (default: `nitrostack.log`). |
| `NITROSTACK_LOG_LEVEL` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `NITROSTACK_LOG_TO_STDOUT` | Set to `true` to allow logging to stdout under stdio transport (Caution: may corrupt protocol stream). |

---

## Transport Options

NitroStack apps can run over three transports, selected via `MCP_TRANSPORT_TYPE` (or `ServerConfig(transport_type=...)`):

- **`stdio`** (default outside production): JSON-RPC over stdin/stdout — the standard mode for desktop MCP clients (Claude Desktop, Cursor, etc.).
- **`http`**: Streamable HTTP + legacy SSE over a real network port, for cloud/remote deployments. Exposes:
  - `POST/GET/DELETE /mcp` — Streamable HTTP (session-based JSON-RPC + SSE streaming)
  - `GET /sse` + `POST /mcp/messages/` — legacy HTTP+SSE for older clients (trailing slash required so messages aren't swallowed by the Streamable HTTP `/mcp` mount)
  - `GET /mcp/health` — health check (`status`, active session count, uptime)
  - Per-session isolation, idle-session timeouts, and DNS-rebinding protection are provided by the underlying `mcp` SDK's `StreamableHTTPSessionManager`; NitroStack adds CORS, a concurrent-session cap, and the health endpoint on top.
  - Task-mode tools that call `context.task.update_progress(...)` push a live `notifications/progress` event over the session's SSE stream (in addition to always being pollable via `tasks/get`) whenever the client sends a `_meta.progressToken` on the `tools/call` request.
- **`dual`** (default in production): runs `stdio` and `http` concurrently as `asyncio` tasks in the same process/event loop — not separate threads — so both share the same `DIContainer` singletons, and uvicorn's signal-based graceful shutdown works correctly (it only installs signal handlers on the main thread). Shutdown is coordinated: either transport stopping (STDIO hitting EOF, or HTTP receiving a termination signal) cleanly stops the other.

Example:
```python
server = ServerConfig(name="my-server", transport_type="http", max_sessions=100, session_timeout_ms=1_800_000)
```

---

## Developing & Testing

### Auto-Registering with Claude Desktop
To automatically configure your server script with Claude Desktop without any manual editing:
```bash
nitrostack-py register --name my-mcp-server --file app.py
```
*(If your scripts folder is not in PATH, use: `python -m nitrostack.cli.main register --name my-mcp-server --file app.py`)*

This detects all standard and Windows Store installation directories, sets up virtualenv executables, and writes the JSON configuration. Once registered, simply restart Claude Desktop.

### Running Tests
To run the automated test suite, execute:
```bash
python tests/test_basic.py
python tests/test_tasks.py
python tests/test_initial_tool.py
python tests/test_transports.py
pytest tests/test_cli.py -v
```

### Testing Harness
Write in-process unit tests using the harness:
```python
import asyncio
from nitrostack.testing import NitroTestingModule
from app import AppModule

async def test_add():
    harness = await NitroTestingModule.create(AppModule)
    result = await harness.call_tool("add", {"input": {"a": 5, "b": 10}})
    assert result == 15.0
    print("Test passed!")

if __name__ == "__main__":
    asyncio.run(test_add())
```
