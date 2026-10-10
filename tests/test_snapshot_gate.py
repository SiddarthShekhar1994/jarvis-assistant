"""Tests for briefing_reader.snapshot_gate: the CONNECT-only gate every connection of the page
pictures' private window goes through.

LOOPBACK ONLY: every test injects the gate's resolver and connector, so no name is looked up and
nothing but 127.0.0.1 is ever connected to (a "public" address the fake resolver gives is only a
label: the fake connector sends it to a local echo server). Invented names only.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
import unittest
from unittest import mock

from briefing_reader import snapshot_gate, snapshots

PUBLIC = "93.184.215.14"   # a public address as a label only: never connected to


class Echo:
    """A local server that echoes what it gets and records how many connections it had."""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.connections = 0
        self.received = b""
        self._stop = False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while not self._stop:
            try:
                conn, _address = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._echo, args=(conn,), daemon=True).start()

    def _echo(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(5)
            try:
                while True:
                    data = conn.recv(4096)
                    if not data:
                        return
                    self.received += data
                    conn.sendall(data.upper())
            except OSError:
                return

    def close(self) -> None:
        self._stop = True
        self.sock.close()


def read_reply(sock: socket.socket) -> bytes:
    """The gate's answer (b"" when it closed or reset the connection without one)."""
    data = b""
    while b"\r\n\r\n" not in data:
        try:
            chunk = sock.recv(1024)
        except ConnectionError:
            break
        if not chunk:
            break
        data += chunk
    return data


def wait_for(predicate, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


class GateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.echo = Echo()
        self.addCleanup(self.echo.close)
        self.resolved: list[str] = []
        self.connected: list[tuple[str, int]] = []
        self.answers: dict[str, list[str]] = {"www.example.org": [PUBLIC], "rebind.example.org": ["127.0.0.1"],
                                              "mixed.example.org": [PUBLIC, "192.168.1.20"],
                                              "v6.example.org": ["2a01:4f8:1:2::1"],
                                              "nat64.example.org": [PUBLIC, "64:ff9b::c0a8:101"]}

    def resolve(self, host: str, port: int) -> list[str]:
        self.resolved.append(host)
        self.assertEqual(port, 443)
        if host not in self.answers:
            raise OSError("no such name")
        return list(self.answers[host])

    def connect(self, address: str, port: int, timeout: float) -> socket.socket:
        self.connected.append((address, port))
        if address != PUBLIC:
            raise OSError("the test reaches only its echo server")
        return socket.create_connection(("127.0.0.1", self.echo.port), timeout=timeout)

    def gate(self, **kwargs) -> snapshot_gate.ConnectGate:
        gate = snapshot_gate.ConnectGate(resolve=self.resolve, connect=self.connect, **kwargs)
        self.addCleanup(gate.close)
        port = gate.start()
        self.assertGreater(port, 0)
        self.assertEqual(gate.start(), port)
        self.assertEqual(gate.port(), port)
        return gate

    def ask(self, gate: snapshot_gate.ConnectGate, head: bytes) -> tuple[socket.socket, bytes]:
        sock = socket.create_connection(("127.0.0.1", gate.port()), timeout=5)
        self.addCleanup(sock.close)
        sock.sendall(head)
        return sock, read_reply(sock)

    def test_a_public_name_on_443_is_tunnelled_both_ways(self) -> None:
        gate = self.gate()
        sock, reply = self.ask(gate, b"CONNECT www.example.org:443 HTTP/1.1\r\nHost: www.example.org:443\r\n\r\n")
        self.assertTrue(reply.startswith(b"HTTP/1.1 200"), reply)
        sock.sendall(b"hello through the tunnel")
        got = b""
        while len(got) < len(b"hello through the tunnel"):
            chunk = sock.recv(1024)
            if not chunk:
                break
            got += chunk
        self.assertEqual(got, b"HELLO THROUGH THE TUNNEL")
        self.assertEqual(self.connected, [(PUBLIC, 443)])
        sock.shutdown(socket.SHUT_WR)          # the page's side ends: the server's side is ended too
        self.assertEqual(sock.recv(1024), b"")
        self.assertTrue(wait_for(lambda: gate.counts() == {"tunnels": 1, "refused": 0, "failed": 0}))

    def test_bytes_sent_with_the_head_are_passed_on(self) -> None:
        gate = self.gate()
        sock, reply = self.ask(gate, b"CONNECT 93.184.215.14:443 HTTP/1.1\r\n\r\nearly")
        self.assertTrue(reply.startswith(b"HTTP/1.1 200"), reply)
        self.assertTrue(wait_for(lambda: self.echo.received == b"early"))
        self.assertEqual(self.resolved, [])   # an address is not looked up

    def test_local_targets_other_ports_and_rebinding_names_are_refused_before_any_connection(self) -> None:
        gate = self.gate()
        # IPv6 never: a global IPv6 address may be this PC's own or a device's on its network (a
        # router at <prefix>::1), a NAT64 one may carry a private IPv4 address - also behind a name.
        for head in (b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n", b"CONNECT [::1]:443 HTTP/1.1\r\n\r\n",
                     b"CONNECT 192.168.1.1:443 HTTP/1.1\r\n\r\n", b"CONNECT printer:443 HTTP/1.1\r\n\r\n",
                     b"CONNECT nas.local:443 HTTP/1.1\r\n\r\n", b"CONNECT www.example.org:80 HTTP/1.1\r\n\r\n",
                     b"CONNECT 93.184.215.14:3478 HTTP/1.1\r\n\r\n",
                     b"CONNECT rebind.example.org:443 HTTP/1.1\r\n\r\n",
                     b"CONNECT mixed.example.org:443 HTTP/1.1\r\n\r\n",
                     b"CONNECT unknown.example.org:443 HTTP/1.1\r\n\r\n",
                     b"CONNECT [2a01:4f8:1:2::1]:443 HTTP/1.1\r\n\r\n",
                     b"CONNECT [64:ff9b::c0a8:101]:443 HTTP/1.1\r\n\r\n",
                     b"CONNECT v6.example.org:443 HTTP/1.1\r\n\r\n",
                     b"CONNECT nat64.example.org:443 HTTP/1.1\r\n\r\n"):
            with self.subTest(head=head):
                sock, reply = self.ask(gate, head)
                self.assertTrue(reply.startswith(b"HTTP/1.1 403"), reply)
                self.assertEqual(sock.recv(1024), b"")
        self.assertEqual(self.connected, [])
        self.assertEqual(self.echo.connections, 0)
        self.assertEqual(sorted(set(self.resolved)), ["mixed.example.org", "nat64.example.org", "rebind.example.org",
                                                      "unknown.example.org", "v6.example.org"])
        self.assertTrue(wait_for(lambda: gate.counts()["refused"] == 14))

    def test_anything_but_connect_is_refused(self) -> None:
        gate = self.gate()
        for head in (b"GET http://www.example.org/ HTTP/1.1\r\nHost: www.example.org\r\n\r\n",
                     b"POST / HTTP/1.1\r\n\r\n", b"CONNECT www.example.org:443\r\n\r\n",
                     b"connect www.example.org:443 HTTP/1.1\r\n\r\n", b"\x16\x03\x01\x00\x10binary\r\n\r\n"):
            with self.subTest(head=head):
                _sock, reply = self.ask(gate, head)
                self.assertTrue(reply.startswith(b"HTTP/1.1 405"), reply)
        self.assertEqual(self.connected, [])
        self.assertEqual(self.resolved, [])

    def test_a_head_that_never_ends_is_dropped(self) -> None:
        gate = self.gate()
        sock = socket.create_connection(("127.0.0.1", gate.port()), timeout=5)
        self.addCleanup(sock.close)
        sock.sendall(b"CONNECT " + b"a" * (snapshot_gate.HEAD_LIMIT + 4096))
        self.assertEqual(read_reply(sock), b"")
        self.assertTrue(wait_for(lambda: gate.counts()["refused"] == 1))

    def test_a_server_that_cannot_be_reached_is_a_bad_gateway(self) -> None:
        self.answers["down.example.org"] = ["93.184.215.15"]   # the fake connector refuses it
        gate = self.gate()
        _sock, reply = self.ask(gate, b"CONNECT down.example.org:443 HTTP/1.1\r\n\r\n")
        self.assertTrue(reply.startswith(b"HTTP/1.1 502"), reply)
        self.assertTrue(wait_for(lambda: gate.counts() == {"tunnels": 0, "refused": 0, "failed": 1}))

    def test_too_many_tunnels_at_once_are_refused(self) -> None:
        gate = self.gate(max_tunnels=1)
        first, reply = self.ask(gate, b"CONNECT www.example.org:443 HTTP/1.1\r\n\r\n")
        self.assertTrue(reply.startswith(b"HTTP/1.1 200"), reply)
        _second, reply = self.ask(gate, b"CONNECT www.example.org:443 HTTP/1.1\r\n\r\n")
        self.assertTrue(reply.startswith(b"HTTP/1.1 503"), reply)
        first.close()
        self.assertTrue(wait_for(lambda: gate.counts()["tunnels"] == 1 and self._free(gate)))

    def _free(self, gate: snapshot_gate.ConnectGate) -> bool:
        try:
            sock = socket.create_connection(("127.0.0.1", gate.port()), timeout=2)
        except OSError:
            return False
        with sock:
            sock.sendall(b"CONNECT www.example.org:443 HTTP/1.1\r\n\r\n")
            return read_reply(sock).startswith(b"HTTP/1.1 200")

    def test_an_idle_tunnel_is_closed(self) -> None:
        gate = self.gate(idle_timeout_s=0.3)
        sock, reply = self.ask(gate, b"CONNECT www.example.org:443 HTTP/1.1\r\n\r\n")
        self.assertTrue(reply.startswith(b"HTTP/1.1 200"), reply)
        sock.settimeout(5)
        self.assertEqual(sock.recv(1024), b"")

    def test_close_ends_open_tunnels_and_stops_accepting(self) -> None:
        gate = self.gate()
        sock, reply = self.ask(gate, b"CONNECT www.example.org:443 HTTP/1.1\r\n\r\n")
        self.assertTrue(reply.startswith(b"HTTP/1.1 200"), reply)
        port = gate.port()
        gate.close()
        sock.settimeout(5)
        try:
            self.assertEqual(sock.recv(1024), b"")
        except ConnectionError:
            pass
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
        self.assertEqual(gate.start(), 0)   # a closed gate does not start again

    def test_the_log_never_gets_a_host_or_an_address(self) -> None:
        with self.assertLogs("briefing_reader.snapshot_gate", logging.DEBUG) as logs:
            logging.getLogger("briefing_reader.snapshot_gate").debug("marker")
            gate = self.gate()
            for head in (b"CONNECT rebind.example.org:443 HTTP/1.1\r\n\r\n",
                         b"CONNECT www.example.org:443 HTTP/1.1\r\n\r\n"):
                sock, _reply = self.ask(gate, head)
                sock.close()
            wait_for(lambda: sum(gate.counts().values()) == 2)
            gate.close()
        text = "\n".join(logs.output) + repr(gate)
        for secret in ("example.org", "127.0.0.1", PUBLIC):
            self.assertNotIn(secret, text)

    def test_a_port_that_cannot_be_bound_gives_zero(self) -> None:
        def refuse(_self, _address):
            raise OSError("in use")

        socket.socket.bind = refuse   # type: ignore[method-assign]
        try:
            gate = snapshot_gate.ConnectGate(resolve=self.resolve, connect=self.connect)
            self.assertEqual(gate.start(), 0)
            self.assertEqual(gate.port(), 0)
        finally:
            del socket.socket.bind   # back to the inherited one


class GateDefaultsTests(unittest.TestCase):
    def test_the_default_lookup_asks_for_ipv4_only_and_lists_each_address_once(self) -> None:
        answers = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:2800:21f:cb07:6820:80da:af6b:8b2c", 443, 0, 0)),
                   (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443)),
                   (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443)),
                   (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.15", 443))]
        with mock.patch.object(socket, "getaddrinfo", return_value=answers) as lookup:   # no real lookup
            found = snapshot_gate._resolve("www.example.org", 443)
        lookup.assert_called_once()
        self.assertEqual(lookup.call_args.kwargs.get("family"), socket.AF_INET)   # no AAAA asked for
        self.assertEqual(found, ["93.184.215.14", "93.184.215.15"])   # an IPv6 answer is never kept

    def test_the_gate_uses_the_rules_of_snapshots(self) -> None:
        self.assertEqual(snapshots.GATE_PORT, 443)
        self.assertEqual(snapshots.tunnel_target("www.example.org:443"), ("www.example.org", ""))
        self.assertTrue(snapshots.resolved_problem(["127.0.0.1"]))


if __name__ == "__main__":
    unittest.main()
