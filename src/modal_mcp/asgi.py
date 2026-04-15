"""ASGI middleware for request-origin validation."""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.status import HTTP_403_FORBIDDEN
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import Settings


class OriginValidationError(ValueError):
    """Raised when an incoming request fails origin validation."""


def _decode_header_value(value: bytes) -> str:
    return value.decode("latin-1").strip()


def _get_header(scope: Scope, name: bytes) -> str | None:
    headers = scope.get("headers")
    if not headers:
        return None
    target = name.lower()
    for key, value in headers:
        if key.lower() == target:
            return _decode_header_value(value)
    return None


def _normalize_host(host: str | None) -> str | None:
    if host is None:
        return None
    candidate = host.strip()
    if not candidate:
        return None
    parsed = urlsplit(f"http://{candidate}")
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.hostname is None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    try:
        if parsed.port is not None:
            pass
    except ValueError:
        return None
    return parsed.hostname.lower()


def _format_origin_host(hostname: str) -> str:
    if ":" in hostname:
        return f"[{hostname}]"
    return hostname


def _normalize_origin(origin: str | None) -> str | None:
    if origin is None:
        return None
    candidate = origin.strip()
    if not candidate or candidate.lower() == "null":
        return None
    parsed = urlsplit(candidate)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname is None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    host = _format_origin_host(parsed.hostname.lower())
    try:
        port = parsed.port
    except ValueError:
        return None
    if (parsed.scheme.lower(), port) in {("http", 80), ("https", 443)}:
        port = None
    if port is None:
        return f"{parsed.scheme.lower()}://{host}"
    return f"{parsed.scheme.lower()}://{host}:{port}"


def _normalized_allowed_hosts(settings: Settings) -> set[str]:
    allowed: set[str] = set()
    for candidate in settings.modal_mcp_allowed_hosts:
        normalized = _normalize_host(candidate)
        if normalized is not None:
            allowed.add(normalized)
    return allowed


def _normalized_allowed_origins(settings: Settings) -> set[str]:
    allowed: set[str] = set()
    for candidate in settings.modal_mcp_allowed_origins:
        normalized = _normalize_origin(candidate)
        if normalized is not None:
            allowed.add(normalized)
    return allowed


def validate_origin(origin: str | None, host: str | None, settings: Settings) -> None:
    """Validate request `Origin` and `Host` headers against configured allowlists."""

    normalized_origin = _normalize_origin(origin)
    if normalized_origin is None:
        if origin is None:
            msg = "missing Origin header"
        elif origin.strip().lower() == "null":
            msg = "null Origin header is not allowed"
        else:
            msg = f"unsupported Origin value: {origin!r}"
        raise OriginValidationError(msg)

    normalized_host = _normalize_host(host)
    if normalized_host is None:
        msg = "missing or malformed Host header"
        raise OriginValidationError(msg)

    allowed_hosts = _normalized_allowed_hosts(settings)
    if normalized_host not in allowed_hosts:
        msg = f"host is not allowlisted: {host!r}"
        raise OriginValidationError(msg)

    allowed_origins = _normalized_allowed_origins(settings)
    if normalized_origin not in allowed_origins:
        msg = f"origin is not allowlisted: {origin!r}"
        raise OriginValidationError(msg)


class OriginGuard:
    """ASGI middleware that rejects untrusted browser origins before MCP handling."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self._app = app
        self._settings = settings

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Validate the request origin before delegating to the wrapped app."""

        if scope["type"] not in {"http", "websocket"}:
            await self._app(scope, receive, send)
            return

        origin = _get_header(scope, b"origin")
        host = _get_header(scope, b"host")
        if host is None:
            server = scope.get("server")
            if isinstance(server, tuple) and server:
                server_host = server[0]
                if isinstance(server_host, str):
                    host = server_host

        try:
            validate_origin(origin, host, self._settings)
        except OriginValidationError:
            await self._reject(scope, send)
            return

        await self._app(scope, receive, send)

    async def _reject(self, scope: Scope, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = b"Forbidden"
        headers: list[tuple[bytes, bytes]] = [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
        await send(
            {
                "type": "http.response.start",
                "status": HTTP_403_FORBIDDEN,
                "headers": headers,
            }
        )
        await send({"type": "http.response.body", "body": body})


__all__ = ["OriginGuard", "OriginValidationError", "validate_origin"]
