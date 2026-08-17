"""
Streamable HTTP transport for NitroStack (Phase 3).

The official `mcp` SDK's `StreamableHTTPSessionManager` already provides
per-session isolation (one transport + one `Server.run()` task per session),
SSE streaming, resumability via an event store, idle-session timeouts, and
DNS-rebinding protection via `TransportSecuritySettings`. This module does
NOT reimplement any of that — it wires those existing features through
NitroStack's config/env vars and adds the handful of pieces the manager does
not provide out of the box:

- CORS headers for browser-based MCP clients
- A concurrent-session cap with a `429` response (the manager has no public
  session-count API or creation hook, so this is tracked via a thin ASGI
  middleware watching the `mcp-session-id` header)
- A `/mcp/health` endpoint
- A root documentation page at `GET /` (browsers opening the HTTP port)
- Chrome DevTools discovery stubs at `GET /json` and `GET /json/version` so
  inspector probes do not 404
- Legacy SSE (`/sse` + `/mcp/messages/`) for older HTTP+SSE-only clients

See `dev-plan/PHASE-3-http-transport.md` for the full scope.
"""
from __future__ import annotations

import contextlib
import html
import logging
import os
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

if TYPE_CHECKING:
    from nitrostack.core.app import McpApplication

logger = logging.getLogger("nitrostack.transports.http")

DEFAULT_ENDPOINT = "/mcp"

# CORS headers for browser-based MCP clients (Inspector).
CORS_ALLOW_HEADERS = [
    "Content-Type",
    "Accept",
    "Authorization",
    "Mcp-Session-Id",
    "MCP-Protocol-Version",
    "Last-Event-ID",
]
CORS_EXPOSE_HEADERS = ["Mcp-Session-Id"]

_PROCESS_START = time.monotonic()


def _server_meta(mcp_app: "McpApplication") -> Dict[str, str]:
    cfg = getattr(mcp_app, "server_config", None)
    return {
        "name": getattr(cfg, "name", None) or "NitroStack MCP Server",
        "version": getattr(cfg, "version", None) or "1.0.0",
    }


def _landing_html(name: str, version: str, endpoint: str) -> str:
    safe_name = html.escape(name)
    safe_version = html.escape(version)
    mcp_path = html.escape(endpoint.rstrip("/") or "/mcp")
    health_path = f"{mcp_path}/health"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{safe_name}</title>
  <style>
    :root {{ color-scheme: dark; }}
    body {{
      margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif;
      background: radial-gradient(circle at 10% 20%, #0f172a 0%, #020617 90%);
      color: #f8fafc;
    }}
    main {{
      width: min(640px, calc(100% - 2rem));
      background: rgba(30, 41, 59, 0.55);
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 16px; padding: 2rem;
    }}
    h1 {{ margin: 0 0 0.35rem; font-size: 1.6rem; }}
    p {{ color: #94a3b8; margin: 0 0 1.25rem; }}
    code, a {{ color: #a5b4fc; }}
    ul {{ margin: 0; padding-left: 1.2rem; line-height: 1.8; }}
  </style>
</head>
<body>
  <main>
    <h1>{safe_name}</h1>
    <p>NitroStack MCP server v{safe_version}. This is not a website — connect with an MCP client.</p>
    <ul>
      <li>Streamable HTTP: <code>POST {mcp_path}</code></li>
      <li>Legacy SSE: <code>GET /sse</code></li>
      <li>Health: <a href="{health_path}"><code>{health_path}</code></a></li>
    </ul>
  </main>
</body>
</html>
"""


def _env_list(name: str) -> List[str]:
    raw = os.environ.get(name)
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


class ExactEndpointSlashMiddleware:
    """
    Internally rewrite `/mcp` → `/mcp/` so Starlette does not 307.

    `Mount("/mcp")` only matches `/mcp/...`. With `redirect_slashes=True`
    (Starlette's default) a request to `/mcp` becomes `307 Location: /mcp/`.
    MCP Inspector's Streamable HTTP client POSTs and GETs `/mcp` with no
    trailing slash; following that 307 on the GET SSE stream drops the
    connection, so List Tools shows a blank page and disconnects.
    """

    def __init__(self, app: ASGIApp, endpoint: str) -> None:
        self.app = app
        self._exact = endpoint.rstrip("/") or "/"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") == self._exact:
            scope = dict(scope)
            scope["path"] = self._exact + "/"
            raw = scope.get("raw_path")
            if isinstance(raw, (bytes, bytearray)):
                scope["raw_path"] = raw.rstrip(b"/") + b"/"
        await self.app(scope, receive, send)


class SessionCapMiddleware:
    """
    Enforce a `max_sessions` concurrent-session cap on the Streamable HTTP
    mount, returning a `429` JSON-RPC error once at capacity.

    `StreamableHTTPSessionManager` doesn't expose a public session count or a
    session-created/closed hook, so this middleware tracks session ids itself
    by observing the `mcp-session-id` request header (existing sessions) and
    the `Mcp-Session-Id` response header (newly created sessions), and prunes
    entries locally after `session_idle_timeout` so the count self-heals even
    if a client disconnects without sending `DELETE`.
    """

    def __init__(
        self,
        app: ASGIApp,
        max_sessions: Optional[int],
        session_idle_timeout: Optional[float] = None,
    ) -> None:
        self.app = app
        self.max_sessions = max_sessions
        self.session_idle_timeout = session_idle_timeout
        self._sessions: Dict[str, float] = {}

    def _prune_stale(self) -> None:
        if not self.session_idle_timeout:
            return
        cutoff = time.monotonic() - self.session_idle_timeout
        for sid in [s for s, seen in self._sessions.items() if seen < cutoff]:
            self._sessions.pop(sid, None)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.max_sessions:
            await self.app(scope, receive, send)
            return

        self._prune_stale()

        headers = dict(scope.get("headers") or [])
        raw_session_id = headers.get(b"mcp-session-id")
        session_id = raw_session_id.decode() if raw_session_id else None
        is_known = session_id is not None and session_id in self._sessions

        if not is_known and len(self._sessions) >= self.max_sessions:
            response = JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32000,
                        "message": "Too many sessions: server at capacity, retry later",
                    },
                    "id": None,
                },
                status_code=429,
            )
            await response(scope, receive, send)
            return

        if is_known:
            self._sessions[session_id] = time.monotonic()

        new_session_id: Dict[str, Optional[str]] = {"value": None}

        async def send_wrapper(message: Any) -> None:
            if message["type"] == "http.response.start":
                for key, value in message.get("headers", []):
                    if key.lower() == b"mcp-session-id":
                        new_session_id["value"] = value.decode()
            await send(message)

        await self.app(scope, receive, send_wrapper)

        if new_session_id["value"]:
            self._sessions[new_session_id["value"]] = time.monotonic()

        if scope.get("method") == "DELETE" and session_id:
            self._sessions.pop(session_id, None)

    @property
    def active_session_count(self) -> int:
        self._prune_stale()
        return len(self._sessions)


def build_http_app(
    mcp_app: "McpApplication",
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    max_sessions: Optional[int] = None,
    session_idle_timeout: Optional[float] = None,
    enable_cors: bool = True,
    stateless: bool = False,
) -> Starlette:
    """
    Build the Starlette app exposing NitroStack's owned low-level server over
    Streamable HTTP (`/mcp`), legacy SSE (`/sse` + `/mcp/messages/`), and a
    health check (`/mcp/health`).

    Args:
        mcp_app: The bootstrapped `McpApplication` whose `mcp_server` (and
            registered tools/resources/prompts) this app serves.
        endpoint: Streamable HTTP mount path (default `/mcp`).
        max_sessions: Optional concurrent-session cap; `None`/`0` disables the cap.
        session_idle_timeout: Optional idle timeout in seconds for stateful
            sessions (ignored when `stateless=True`).
        enable_cors: Whether to add permissive CORS headers (default `True`).
            When `False`, DNS-rebinding
            protection (Origin/Host validation) is enabled instead, configured
            via `MCP_ALLOWED_HOSTS`/`MCP_ALLOWED_ORIGINS` env vars.
        stateless: When `True`, every request gets a fresh transport with no
            session id and no session tracking (see `StreamableHTTPSessionManager`).
            This is the primitive the `2026-07-28` stateless MCP spec needs;
            exposed here as a config knob so adopting that spec later doesn't
            require touching this wiring again.
    """
    security_settings = None
    if not enable_cors:
        allowed_hosts = _env_list("MCP_ALLOWED_HOSTS") or ["localhost:*", "127.0.0.1:*"]
        allowed_origins = _env_list("MCP_ALLOWED_ORIGINS")
        security_settings = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
        )

    session_manager = StreamableHTTPSessionManager(
        app=mcp_app.mcp_server,
        stateless=stateless,
        session_idle_timeout=None if stateless else session_idle_timeout,
        security_settings=security_settings,
    )
    # Trailing slash is required: Starlette's Mount("/mcp/messages") only matches
    # "/mcp/messages/" (or deeper), NOT bare "/mcp/messages". Without the slash the
    # endpoint event points at a path that falls through to Mount("/mcp") (Streamable
    # HTTP), which then 406s SSE clients that don't send Streamable Accept headers.
    # Matches the mcp SDK's own SseServerTransport example ("/messages/").
    sse_messages_path = f"{endpoint.rstrip('/')}/messages/"
    sse_transport = SseServerTransport(sse_messages_path)

    async def handle_streamable_http(scope: Scope, receive: Receive, send: Send) -> None:
        await session_manager.handle_request(scope, receive, send)

    mcp_asgi_app: ASGIApp = handle_streamable_http
    session_cap: Optional[SessionCapMiddleware] = None
    if max_sessions and not stateless:
        session_cap = SessionCapMiddleware(
            mcp_asgi_app, max_sessions=max_sessions, session_idle_timeout=session_idle_timeout
        )
        mcp_asgi_app = session_cap

    async def handle_sse(request):
        async with sse_transport.connect_sse(request.scope, request.receive, request._send) as streams:
            await mcp_app.mcp_server.run(streams[0], streams[1], mcp_app.mcp_server.create_initialization_options())
        return Response()

    async def health_check(request):
        return JSONResponse(
            {
                "status": "ok",
                "transport": "streamable-http",
                "protocolVersion": "2025-06-18",
                "stateless": stateless,
                "sessions": session_cap.active_session_count if session_cap else None,
                "uptimeSeconds": round(time.monotonic() - _PROCESS_START, 2),
            }
        )

    async def root_page(request):
        meta = _server_meta(mcp_app)
        return HTMLResponse(_landing_html(meta["name"], meta["version"], endpoint))

    async def json_version(request):
        """Chrome/Cursor DevTools probe `/json/version` when a tab opens localhost."""
        meta = _server_meta(mcp_app)
        health_path = f"{endpoint.rstrip('/')}/health"
        return JSONResponse(
            {
                "Browser": meta["name"],
                "Protocol-Version": "2025-06-18",
                "User-Agent": f"NitroStack/{meta['version']}",
                "webSocketDebuggerUrl": "",
                "transport": "mcp",
                "endpoints": {
                    "mcp": endpoint,
                    "sse": "/sse",
                    "health": health_path,
                },
            }
        )

    async def json_list(request):
        """Chrome DevTools `/json` and `/json/list` expect a page list; empty = not CDP."""
        return JSONResponse([])

    async def favicon(request):
        return Response(status_code=204)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with session_manager.run():
            logger.info(
                "StreamableHTTP session manager started (stateless=%s, max_sessions=%s, "
                "session_idle_timeout=%s)",
                stateless,
                max_sessions,
                session_idle_timeout,
            )
            yield

    routes = [
        # More specific paths MUST come before the catch-all `Mount(endpoint, ...)`
        # below — Starlette matches routes in order, and a `Mount` matches any
        # path under its prefix, so `/mcp/health` would otherwise be swallowed
        # by the `/mcp` mount before ever reaching the health route.
        Route("/", endpoint=root_page, methods=["GET"]),
        Route("/json/version", endpoint=json_version, methods=["GET"]),
        Route("/json/list", endpoint=json_list, methods=["GET"]),
        Route("/json", endpoint=json_list, methods=["GET"]),
        Route("/favicon.ico", endpoint=favicon, methods=["GET"]),
        Route(f"{endpoint}/health", endpoint=health_check, methods=["GET"]),
        Mount(sse_messages_path, app=sse_transport.handle_post_message),
        # `handle_streamable_http`/`sse_transport.handle_post_message` are raw
        # ASGI apps (scope, receive, send), so they must be `Mount`ed rather
        # than used as `Route` endpoints (which expect `Request -> Response`).
        Mount(endpoint, app=mcp_asgi_app),
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
    ]

    # Rewrite `/mcp` → `/mcp/` *before* routing so Inspector never sees a 307.
    # CORS stays outermost so preflight still works on the original path.
    middleware = [
        Middleware(ExactEndpointSlashMiddleware, endpoint=endpoint),
    ]
    if enable_cors:
        middleware.insert(
            0,
            Middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                allow_headers=CORS_ALLOW_HEADERS,
                expose_headers=CORS_EXPOSE_HEADERS,
            ),
        )

    return Starlette(routes=routes, middleware=middleware, lifespan=lifespan)
