"""Page pictures' network gate (Qt-free): the one way the hidden private window reaches the network.

    ConnectGate(resolve=, connect=)   start() -> the port on 127.0.0.1 (0: it could not start);
                                      port(); counts() (tunnels / refused / failed); close()

snapshot_engine starts Chromium with --proxy-server=127.0.0.1:<port> and --proxy-bypass-list=<-loopback>
(snapshots.GATE_FLAGS), so every connection of a pictured page comes here first: its own requests,
and what the request interceptor never sees (a pre-connection, the connection to a blocked
redirect's target, WebRTC's TURN over TCP). The gate answers only HTTP CONNECT, and only to port 443
(snapshots.tunnel_target: a public IPv4 address or a public name). A name is looked up here (IPv4
only) and the gate connects only when every address it resolved to is a public IPv4 address
(snapshots.resolved_problem), to that very address: a public name that leads to this PC or its
network (DNS rebinding) is refused too, and no IPv6 address is ever connected to (a global one may
be this PC's own or a device's on its network). Chromium itself looks no name up
(snapshots.RESOLVER_RULES). With a proxy set Chromium sends no QUIC (WebTransport), and with
--force-webrtc-ip-handling-policy=disable_non_proxied_udp no WebRTC UDP either.

Threads: one accept thread and one thread per connection (daemon threads, at most MAX_TUNNELS
connections at once; more are refused). Bytes inside a tunnel are passed on as they are (the page's
TLS: nothing is read or kept). Memory only; nothing is written to a file; the log never gets a host,
an address or a byte of a connection (counts only, and only at debug level). Never raises out of a
thread; the gate lives until Jarvis closes (Chromium keeps its proxy setting for the process): from
the first page picture on, a port on 127.0.0.1 that any program on this PC could also use - to
reach public https servers only, under these same rules (the README says so).
"""

from __future__ import annotations

import ipaddress
import logging
import select
import socket
import threading
from collections.abc import Callable
from typing import Any

from . import snapshots

logger = logging.getLogger(__name__)

__all__ = ["ConnectGate", "MAX_TUNNELS", "HEAD_LIMIT"]

MAX_TUNNELS = 48                # connections at once (Chromium opens at most 32 per proxy)
HEAD_LIMIT = 8192               # bytes of a CONNECT request's head
HEAD_TIMEOUT_S = 10.0           # to send the head
CONNECT_TIMEOUT_S = 6.0         # to reach the page's server (per address; a shot has 15 s in all)
IDLE_TIMEOUT_S = 60.0           # a tunnel with no byte either way for this long is closed
SEND_TIMEOUT_S = 30.0           # one side not reading for this long ends the tunnel
_CHUNK = 65536
_REPLIES = {
    200: b"HTTP/1.1 200 Connection established\r\n\r\n",
    403: b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
    405: b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
    502: b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
    503: b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
}


def _resolve(host: str, port: int) -> list[str]:
    """Every IPv4 address ``host`` resolves to for a TCP connection, once each, in the resolver's
    order. Never an IPv6 one (no AAAA lookup): a global IPv6 address may be this PC's own or a
    device's on its network, which snapshots.resolved_problem could not tell from an internet host."""
    found: list[str] = []
    for family, _kind, _proto, _name, address in socket.getaddrinfo(host, port, family=socket.AF_INET,
                                                                    type=socket.SOCK_STREAM):
        text = str(address[0])
        if family == socket.AF_INET and text not in found:
            found.append(text)
    return found


def _connect(address: str, port: int, timeout: float) -> socket.socket:
    return socket.create_connection((address, port), timeout=timeout)


def _is_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _close(sock: Any) -> None:
    try:
        sock.close()
    except Exception:  # noqa: BLE001
        pass


class ConnectGate:
    """A CONNECT-only proxy on 127.0.0.1 that tunnels to public addresses on port 443 only.

    ``resolve(host, port) -> [address, ...]`` and ``connect(address, port, timeout) -> socket`` are
    the seams for tests (the defaults use this PC's resolver and a plain TCP connection).
    """

    def __init__(self, *, resolve: Callable[[str, int], list[str]] | None = None,
                 connect: Callable[[str, int, float], socket.socket] | None = None,
                 max_tunnels: int = MAX_TUNNELS, idle_timeout_s: float = IDLE_TIMEOUT_S) -> None:
        self._resolve = resolve or _resolve
        self._connect = connect or _connect
        self._idle_s = max(0.1, float(idle_timeout_s))
        self._slots = threading.BoundedSemaphore(max(1, int(max_tunnels)))
        self._lock = threading.Lock()
        self._listener: socket.socket | None = None
        self._port = 0
        self._stopped = threading.Event()
        self._open: set[socket.socket] = set()
        self._counts = {"tunnels": 0, "refused": 0, "failed": 0}

    def __repr__(self) -> str:   # counts only
        return f"ConnectGate(port={self._port}, counts={self.counts()})"

    # ---- life ------------------------------------------------------------------------------

    def start(self) -> int:
        """Listen on 127.0.0.1 (a free port) and serve; the port, or 0 when it could not start.
        Starting again returns the same port. Never raises."""
        with self._lock:
            if self._stopped.is_set():
                return 0
            if self._port:
                return self._port
            listener = None
            try:
                listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                listener.bind(("127.0.0.1", 0))
                listener.listen(64)
                port = int(listener.getsockname()[1])
                thread = threading.Thread(target=self._accept_loop, args=(listener,), name="jarvis-page-gate",
                                          daemon=True)
                thread.start()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Page pictures' gate could not start (%s)", type(exc).__name__)
                if listener is not None:
                    _close(listener)
                return 0
            self._listener = listener
            self._port = port
            return port

    def port(self) -> int:
        return self._port

    def counts(self) -> dict[str, int]:
        """tunnels (opened) / refused / failed (the server could not be reached): counts only."""
        with self._lock:
            return dict(self._counts)

    def close(self) -> None:
        """Stop accepting and end every open tunnel. Never raises."""
        self._stopped.set()
        with self._lock:
            listener, self._listener = self._listener, None
            open_now = list(self._open)
            self._open.clear()
        if listener is not None:
            _close(listener)
        for sock in open_now:
            _close(sock)

    # ---- threads ---------------------------------------------------------------------------

    def _add(self, name: str) -> None:
        with self._lock:
            self._counts[name] += 1

    def _track(self, sock: socket.socket) -> bool:
        with self._lock:
            if self._stopped.is_set():
                return False
            self._open.add(sock)
            return True

    def _untrack(self, sock: socket.socket | None) -> None:
        if sock is None:
            return
        with self._lock:
            self._open.discard(sock)
        _close(sock)

    def _accept_loop(self, listener: socket.socket) -> None:
        errors = 0
        while not self._stopped.is_set():
            try:
                client, _address = listener.accept()
            except OSError:
                if self._stopped.is_set():
                    return
                errors += 1
                if errors > 20:   # the socket is gone: Chromium's loads fail, nothing is reached
                    logger.debug("Page pictures' gate stopped accepting")
                    return
                self._stopped.wait(0.05)
                continue
            errors = 0
            if not self._slots.acquire(blocking=False):
                self._reply_and_close(client, 503)
                self._add("refused")
                continue
            try:
                threading.Thread(target=self._serve, args=(client,), name="jarvis-page-gate-tunnel",
                                 daemon=True).start()
            except Exception:  # noqa: BLE001 - no thread: refuse this one
                self._slots.release()
                self._reply_and_close(client, 503)
                self._add("refused")

    def _reply_and_close(self, client: socket.socket, code: int) -> None:
        """Answer a connection the gate takes no more of, reading its head away first (briefly):
        closing a socket with unread bytes would reset it before the answer is read."""
        try:
            client.settimeout(0.2)
            client.sendall(_REPLIES[code])
            client.shutdown(socket.SHUT_WR)
            client.recv(HEAD_LIMIT)
        except Exception:  # noqa: BLE001
            pass
        _close(client)

    def _read_head(self, client: socket.socket) -> tuple[bytes, bytes] | None:
        data = b""
        while b"\r\n\r\n" not in data:
            if len(data) > HEAD_LIMIT:
                return None
            chunk = client.recv(4096)
            if not chunk:
                return None
            data += chunk
        head, _sep, rest = data.partition(b"\r\n\r\n")
        return head, rest

    def _serve(self, client: socket.socket) -> None:
        upstream: socket.socket | None = None
        try:
            if not self._track(client):
                _close(client)
                return
            client.settimeout(HEAD_TIMEOUT_S)
            got = self._read_head(client)
            if got is None:
                self._add("refused")
                return
            head, rest = got
            parts = head.split(b"\r\n", 1)[0].decode("ascii", "replace").split()
            if len(parts) != 3 or parts[0] != "CONNECT" or not parts[2].startswith("HTTP/1."):
                self._add("refused")
                client.sendall(_REPLIES[405])
                return
            host, problem = snapshots.tunnel_target(parts[1])
            addresses: list[str] = []
            if not problem:
                if _is_address(host):
                    addresses = [host]   # an IP address is not looked up
                else:
                    try:
                        addresses = list(self._resolve(host, snapshots.GATE_PORT))
                    except Exception:  # noqa: BLE001 - not resolved: refused below
                        addresses = []
                problem = snapshots.resolved_problem(addresses)
            if problem:
                self._add("refused")
                client.sendall(_REPLIES[403])
                return
            for address in addresses:
                try:
                    upstream = self._connect(address, snapshots.GATE_PORT, CONNECT_TIMEOUT_S)
                    break
                except Exception:  # noqa: BLE001 - try the next address
                    upstream = None
            if upstream is None or not self._track(upstream):
                self._add("failed")
                client.sendall(_REPLIES[502])
                return
            self._add("tunnels")
            for sock in (client, upstream):
                sock.settimeout(SEND_TIMEOUT_S)
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except OSError:
                    pass
            client.sendall(_REPLIES[200])
            if rest:
                upstream.sendall(rest)
            self._pump(client, upstream)
        except Exception:  # noqa: BLE001 - a connection's end, whatever it was
            pass
        finally:
            self._untrack(upstream)
            self._untrack(client)
            self._slots.release()

    def _pump(self, client: socket.socket, upstream: socket.socket) -> None:
        """Pass bytes both ways until both sides ended (or idle, or the gate closed)."""
        other = {client: upstream, upstream: client}
        reading = [client, upstream]
        while reading and not self._stopped.is_set():
            ready, _w, _x = select.select(reading, [], [], self._idle_s)
            if not ready:
                return   # idle
            for sock in ready:
                data = sock.recv(_CHUNK)
                if data:
                    other[sock].sendall(data)
                    continue
                reading.remove(sock)   # this side ended: tell the other side, keep reading it
                try:
                    other[sock].shutdown(socket.SHUT_WR)
                except OSError:
                    return
