"""ASGI middleware for request-origin validation."""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlsplit

from starlette.status import HTTP_403_FORBIDDEN
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import ConfigError


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


_NORMALIZERS = {
    "origin": _normalize_origin,
    "host": _normalize_host,
}
_ENV_VAR = {
    "origin": "MODAL_MCP_ALLOWED_ORIGINS",
    "host": "MODAL_MCP_ALLOWED_HOSTS",
}


class OriginGuard:
    """ASGI middleware that rejects untrusted browser origins before MCP handling.

    Allowed origins and hosts are normalised once at construction time and stored
    as ``frozenset[str]``.  The per-request hot path is two header lookups plus
    two set-membership checks; the URL parser is never invoked over the
    *configured* allowlists per request.

    Malformed allowlist entries raise :class:`ConfigError` at startup, naming the
    bad value, so configuration mistakes surface loudly rather than silently
    rejecting every subsequent request.
    """

    __slots__ = ("_allowed_hosts", "_allowed_origins", "_app")

    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_origins: Sequence[str],
        allowed_hosts: Sequence[str],
    ) -> None:
        self._app = app
        self._allowed_origins = self._build_allowed_set(allowed_origins, kind="origin")
        self._allowed_hosts = self._build_allowed_set(allowed_hosts, kind="host")

    @staticmethod
    def _build_allowed_set(
        entries: Sequence[str],
        *,
        kind: str,
    ) -> frozenset[str]:
        """Normalise and validate ``entries`` once at construction.

        Raises :class:`ConfigError` on any bad input. ``kind`` is ``"origin"``
        or ``"host"`` and selects which normaliser and which env-var name to
        mention in the error message.
        """

        normalise = _NORMALIZERS[kind]
        env_var = _ENV_VAR[kind]
        normalised: set[str] = set()
        for raw in entries:
            value = normalise(raw)
            if value is None:
                msg = f"{env_var} entry is not a valid {kind}: {raw!r}"
                raise ConfigError(msg)
            normalised.add(value)
        return frozenset(normalised)

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

        origin = _normalize_origin(_get_header(scope, b"origin"))
        if origin is None or origin not in self._allowed_origins:
            await self._reject(scope, send)
            return

        host = _normalize_host(_get_header(scope, b"host"))
        if host is None or host not in self._allowed_hosts:
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


__all__ = ["OriginGuard", "OriginValidationError"]
