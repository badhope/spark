"""Remote-access helpers: QR links, an optional LAN listener, and public hosts.

The Web UI has always been able to *serve* a remote browser, but there was no
single action that turned the control plane into something a phone could open.
This module owns that missing layer:

* normalize an operator-supplied public origin (``https://spark.example.com``);
* recognise requests that arrived through that public origin so they cannot
  inherit the loopback control role just because a reverse proxy dials 127.0.0.1;
* start/stop a second HTTP listener bound to the LAN only when the main server
  is loopback-only;
* render a QR data URL for the one-time pairing link.

Nothing here exposes the process by itself. The caller must ask for a link, and
the link is protected by ``DevicePairing``'s short-lived one-time code.
"""
from __future__ import annotations

import base64
import ipaddress
import logging
import socket
import threading
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class RemoteAccessError(Exception):
    """A remote-access request that must be shown to the user as a 4xx."""


def normalize_public_url(value: str) -> str:
    """Return a bare ``scheme://host[:port]`` origin, or ``""``.

    A public origin is used in security comparisons, so paths, query strings,
    credentials and fragments are rejected rather than silently dropped.
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RemoteAccessError("public URL must be an http(s) origin")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RemoteAccessError("public URL cannot contain credentials or a query")
    if parsed.path not in {"", "/"}:
        raise RemoteAccessError("public URL must not contain a path")
    host = parsed.hostname
    if "%" in host:
        raise RemoteAccessError("public URL cannot contain an encoded host")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        # A public origin pointing at loopback is a configuration mistake and
        # would make every request look local. Refuse it early.
        if ipaddress.ip_address(host).is_loopback:
            raise RemoteAccessError("public URL cannot point at loopback")
    netloc = f"[{host}]" if ":" in host else host
    try:
        port = parsed.port
    except ValueError as exc:
        raise RemoteAccessError(f"invalid public URL port: {exc}") from exc
    if port is not None:
        netloc = f"{netloc}:{port}"
    return f"{parsed.scheme}://{netloc}"


def _host_port_matches(host_port: int | None, expected: int) -> bool:
    """Host headers often omit the default port; treat that as the expected one."""
    return host_port is None or host_port == expected


class RemoteAccessManager:
    """Owns the public origin and the optional LAN listener for one Web state."""

    def __init__(self, state: Any) -> None:
        self._state = state
        self._lock = threading.RLock()
        self._public_url = ""
        self._lan_server: Any = None
        self._lan_thread: threading.Thread | None = None
        self._lan_port = 0
        self._lan_ip = ""
        self._lan_error = ""

    # -- public origin -------------------------------------------------
    def set_public_url(self, value: str) -> None:
        self._public_url = normalize_public_url(value)

    @property
    def public_url(self) -> str:
        return self._public_url

    @property
    def public_hostname(self) -> str:
        if not self._public_url:
            return ""
        parsed = urlparse(self._public_url)
        return (parsed.hostname or "").lower()

    @property
    def public_port(self) -> int | None:
        if not self._public_url:
            return None
        parsed = urlparse(self._public_url)
        return parsed.port or (443 if parsed.scheme == "https" else 80)

    @property
    def public_scheme(self) -> str:
        return urlparse(self._public_url).scheme if self._public_url else ""

    def is_public_host(self, hostname: str, port: int | None) -> bool:
        """Whether a Host header belongs to the configured public origin."""
        if not self._public_url:
            return False
        return hostname.strip("[]").lower() == self.public_hostname and _host_port_matches(
            port, self.public_port or 0
        )

    def is_public_origin(self, scheme: str, hostname: str, port: int | None) -> bool:
        if not self._public_url:
            return False
        return (
            scheme == self.public_scheme
            and hostname.strip("[]").lower() == self.public_hostname
            and _host_port_matches(port, self.public_port or 0)
        )

    def is_public_request(self, handler: Any) -> bool:
        host = (handler.headers.get("Host") or "").strip()
        if not host:
            return False
        try:
            parsed = urlparse(f"http://{host}")
            return self.is_public_host(parsed.hostname or "", parsed.port)
        except ValueError:
            return False

    def is_lan_host(self, hostname: str, port: int | None) -> bool:
        """Whether a Host header belongs to the currently advertised LAN URL.

        The LAN listener is a second server on ``0.0.0.0``.  It must accept the
        Host a phone sends (the LAN IP and its own port) before the one-time
        pairing claim, which is unauthenticated by design.
        """
        url = self.lan_url()
        if not url:
            return False
        try:
            parsed = urlparse(url)
        except ValueError:
            return False
        expected_port = parsed.port or 80
        return (
            hostname.strip("[]").lower() == (parsed.hostname or "").lower()
            and _host_port_matches(port, expected_port)
        )

    def is_lan_origin(self, scheme: str, hostname: str, port: int | None) -> bool:
        """Whether an Origin belongs to the advertised LAN URL."""
        if scheme != "http":
            return False
        return self.is_lan_host(hostname, port)

    # -- local network -------------------------------------------------
    @staticmethod
    def _detect_lan_ipv4() -> str:
        """Find a non-loopback IPv4 without sending a packet.

        Connecting a UDP socket only selects a route; it does not emit traffic.
        Fall back to hostname resolution for offline machines.
        """
        candidates: list[str] = []
        for target in ("8.8.8.8", "1.1.1.1"):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.settimeout(0.2)
                    sock.connect((target, 80))
                    candidates.append(sock.getsockname()[0])
            except OSError:
                continue
        try:
            candidates.append(socket.gethostbyname(socket.gethostname()))
        except OSError:
            pass
        for value in candidates:
            try:
                ip = ipaddress.ip_address(value)
            except ValueError:
                continue
            if ip.version == 4 and not ip.is_loopback and not ip.is_link_local:
                return value
        return ""

    def _main_lan_url(self) -> str:
        host = str(getattr(self._state, "web_host", "") or "")
        port = int(getattr(self._state, "web_port", 0) or 0)
        if not port or host in {"", "127.0.0.1", "localhost", "::1"}:
            return ""
        if host == "0.0.0.0":
            ip = self._detect_lan_ipv4()
            if not ip:
                return ""
            return f"http://{ip}:{port}"
        if host.startswith("/") or host == "::":
            return ""
        return f"http://{host}:{port}"

    def lan_url(self) -> str:
        main = self._main_lan_url()
        if main:
            return main
        with self._lock:
            if self._lan_server is not None and self._lan_ip and self._lan_port:
                return f"http://{self._lan_ip}:{self._lan_port}"
        return ""

    @property
    def lan_running(self) -> bool:
        return bool(self._main_lan_url()) or self._lan_server is not None

    def start_lan(self) -> str:
        """Start the loopback workaround listener and return its LAN URL."""
        main = self._main_lan_url()
        if main:
            return main
        with self._lock:
            if self._lan_server is not None:
                url = self.lan_url()
                if url:
                    return url
                raise RemoteAccessError(self._lan_error or "LAN listener is not ready")
            handler_class = getattr(self._state, "handler_class", None)
            ui_dir = getattr(self._state, "ui_dir", None)
            if handler_class is None:
                raise RemoteAccessError("Web handler is not initialized")
            ip = self._detect_lan_ipv4()
            if not ip:
                raise RemoteAccessError(
                    "没有检测到局域网 IPv4 地址；请用 --host 0.0.0.0 启动或配置 --public-url"
                )
            from spark.web.server import SparkHTTPServer

            try:
                server = SparkHTTPServer(("0.0.0.0", 0), handler_class)
            except OSError as exc:
                raise RemoteAccessError(f"无法启动局域网监听：{exc}") from exc
            server.state = self._state
            server.ui_dir = ui_dir
            thread = threading.Thread(
                target=server.serve_forever,
                name="spark-web-lan",
                daemon=True,
            )
            thread.start()
            self._lan_server = server
            self._lan_thread = thread
            self._lan_ip = ip
            self._lan_port = int(server.server_port)
            self._lan_error = ""
            logger.info("Spark remote LAN listener at http://%s:%d", ip, self._lan_port)
            return f"http://{ip}:{self._lan_port}"

    def stop_lan(self) -> None:
        with self._lock:
            server = self._lan_server
            thread = self._lan_thread
            self._lan_server = None
            self._lan_thread = None
            self._lan_port = 0
            self._lan_ip = ""
        if server is None:
            return
        try:
            server.shutdown()
        except Exception:  # pragma: no cover - teardown must not raise
            logger.debug("LAN listener shutdown failed", exc_info=True)
        try:
            server.server_close()
        except Exception:  # pragma: no cover - teardown must not raise
            logger.debug("LAN listener close failed", exc_info=True)
        if thread is not None:
            try:
                thread.join(timeout=2)
            except Exception:  # pragma: no cover
                logger.debug("LAN listener join failed", exc_info=True)

    def close(self) -> None:
        self.stop_lan()

    # -- status / links ------------------------------------------------
    def status(self, *, device_count: int = 0) -> dict[str, Any]:
        lan_url = self.lan_url()
        public = self.public_url
        mode = "public" if public else ("lan" if lan_url else "local")
        if public:
            warning = (
                "公网入口必须由你自己的 HTTPS 反向代理转发，并保留原始 Host；"
                "不要在公网直接暴露本机端口。"
            )
        elif lan_url:
            warning = "当前链接只在同一局域网内可用；外网访问需要配置 --public-url。"
        else:
            warning = "当前仅监听本机；生成二维码时会自动开启局域网监听。"
        return {
            "mode": mode,
            "public_url": public,
            "public_configured": bool(public),
            "lan": {
                "running": bool(lan_url),
                "url": lan_url,
            },
            "server": {
                "host": str(getattr(self._state, "web_host", "") or ""),
                "port": int(getattr(self._state, "web_port", 0) or 0),
            },
            "devices": int(device_count),
            "reachable": bool(public or lan_url),
            "warning": warning,
        }

    def ensure_base(self, mode: str = "auto") -> dict[str, Any]:
        """Resolve the base URL for a QR link and start LAN when necessary."""
        clean = (mode or "auto").strip().lower()
        if clean not in {"auto", "public", "lan"}:
            raise RemoteAccessError("mode must be auto, public or lan")
        public = self.public_url
        if clean == "public":
            if not public:
                raise RemoteAccessError(
                    "没有配置公网入口；请用 spark web --public-url https://你的域名 启动"
                )
            return {"mode": "public", "base_url": public, "lan_url": self.lan_url()}
        if clean == "auto" and public:
            return {"mode": "public", "base_url": public, "lan_url": self.lan_url()}
        lan_url = self.start_lan()
        return {"mode": "lan", "base_url": lan_url, "lan_url": lan_url}


def qr_data_url(url: str) -> str:
    """Render *url* as a self-contained SVG data URI.

    ``qrcode`` is an optional dependency: if an older installation lacks it,
    callers get a clear error instead of a broken image.
    """
    try:
        import qrcode
        from qrcode.image.svg import SvgPathImage
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RemoteAccessError("服务器缺少 qrcode 依赖，无法生成二维码") from exc
    image = qrcode.make(url, image_factory=SvgPathImage, box_size=10, border=2)
    svg = image.to_string()
    if isinstance(svg, bytes):
        raw = svg
    else:
        raw = str(svg).encode("utf-8")
    return "data:image/svg+xml;base64," + base64.b64encode(raw).decode("ascii")
