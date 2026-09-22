#!/usr/bin/env python3
"""D23 L5 fault-injecting proxy for the Phase 4 REST live harness (ticket #20).

One stdlib-only TCP/HTTP hop between ``ignition-rest`` and the Gateway. It is the
only place the harness can produce a transport failure the *server* has to
classify, because everything else in the live environment is healthy: a refused
connect, a connection dropped while the request body is being written, a
connection dropped once the body is complete but before the response is relayed,
and a response held back past the deployment's deadline.

Each fault is armed over a small HTTP control port and applies to the next
request that matches it, so a case is exactly:

    arm(mode, method=PUT) -> call the Tool -> read /state -> judge

``/state`` is the evidence: per-request ``method``/``target`` plus whether the
request reached the Gateway, whether its body completed, and what the proxy did
with it. A request the proxy never forwarded is a request the Gateway never saw,
which is what makes the ``NOT_SENT`` and mid-body cases statements about Ignition
and not only about the client.

Three deliberate properties:

- **Refusing the connect needs the listener itself to go away.** A TCP RST after
  ``accept()`` is *not* a refused connect: the client's ``connect()`` already
  succeeded, so it classifies the attempt as possibly dispatched. ``connect_refused``
  therefore closes the data listener (and reopens it when another mode is armed),
  which is a genuine ``ECONNREFUSED`` on the socket the server dials.
- **One request per connection.** The proxy answers one request per client
  connection and relays the response until the upstream closes (it sends
  ``Connection: close`` upstream). Per-request fault accounting stays unambiguous,
  and it is invisible to Ignition, which sees an ordinary single-request HTTP/1.1
  connection. The client's own ``Connection`` header is replaced, never duplicated.
- **Drops happen after the Gateway has answered.** ``drop_after_body`` relays the
  whole request *and* reads the upstream response before killing the connection, so
  the case is deterministic: the Gateway applied the change and the caller still
  cannot learn anything from the exchange.

The proxy never retries, never replays and never rewrites a body: it forwards
bytes verbatim or drops them.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
import json
import logging
import signal
import socket
import struct
from typing import Any

LOGGER = logging.getLogger("fault_proxy")

MODE_NONE = "none"
#: Close the data listener: the next connect is a genuine ``ECONNREFUSED``.
MODE_CONNECT_REFUSED = "connect_refused"
#: Read part of the request body, then RST the connection without dialling upstream.
MODE_DROP_MID_BODY = "drop_mid_body"
#: Relay the whole request, read the answer, then RST the connection instead of relaying it.
MODE_DROP_AFTER_BODY = "drop_after_body"
#: Relay the request normally and hold the answer back for ``delaySeconds``.
MODE_DELAY_RESPONSE = "delay_response"
#: Forward ``afterCount`` matching requests, then close the data listener, so the *next*
#: connect — the write itself — is a genuine ``ECONNREFUSED``. A Tool that exports before
#: it dispatches (D16's ``project_import`` reads a baseline and a pre-import re-export)
#: cannot be refused a connect by arming ``connect_refused``: that would kill the reads
#: the transaction needs first.
MODE_REFUSE_AFTER_FORWARD = "refuse_after_forward"
MODES = (
    MODE_NONE, MODE_CONNECT_REFUSED, MODE_DROP_MID_BODY, MODE_DROP_AFTER_BODY,
    MODE_DELAY_RESPONSE, MODE_REFUSE_AFTER_FORWARD,
)
#: Modes that apply to one request. ``connect_refused`` is a listener state and stays
#: armed until it is replaced.
ONE_SHOT_MODES = (
    MODE_DROP_MID_BODY, MODE_DROP_AFTER_BODY, MODE_DELAY_RESPONSE, MODE_REFUSE_AFTER_FORWARD,
)

MAX_HEAD_BYTES = 65_536
MAX_RECORDED_REQUESTS = 200
#: How much of the body a mid-body drop lets through before it kills the connection.
DEFAULT_MID_BODY_BYTES = 8
#: Bound on the upstream answer the proxy buffers before dropping or delaying it.
MAX_BUFFERED_RESPONSE_BYTES = 8 * 1024 * 1024


class ProxyError(RuntimeError):
    """A harness-side misuse: never one of the faults the proxy injects."""


@dataclass(slots=True)
class Fault:
    """One armed fault: which requests it matches and what it does to them."""

    mode: str = MODE_NONE
    method: str = ""
    path_contains: str = ""
    delay_seconds: float = 0.0
    mid_body_bytes: int = DEFAULT_MID_BODY_BYTES
    #: ``refuse_after_forward`` only: how many matching requests to answer before the
    #: data listener goes away.
    after_count: int = 1
    one_shot: bool = True
    seen: int = 0

    def matches(self, method: str, target: str) -> bool:
        if self.method and self.method.upper() != method.upper():
            return False
        return not (self.path_contains and self.path_contains not in target)

    def public(self) -> dict[str, Any]:
        return {
            "mode": self.mode, "method": self.method, "pathContains": self.path_contains,
            "delaySeconds": self.delay_seconds, "midBodyBytes": self.mid_body_bytes,
            "afterCount": self.after_count, "normalized": self.seen, "oneShot": self.one_shot,
        }


@dataclass(slots=True)
class ProxyState:
    """What is armed, and what the proxy did with each request it saw."""

    armed: Fault = field(default_factory=Fault)
    listening: bool = True
    counters: dict[str, int] = field(default_factory=dict)
    requests: list[dict[str, Any]] = field(default_factory=list)

    def bump(self, name: str, by: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + by

    def record(self, entry: dict[str, Any]) -> None:
        self.requests.append(entry)
        del self.requests[:-MAX_RECORDED_REQUESTS]

    def snapshot(self) -> dict[str, Any]:
        return {
            "armed": None if self.armed.mode == MODE_NONE else self.armed.public(),
            "listening": self.listening,
            "requestsSeen": self.counters.get("requests_seen", 0),
            "counters": dict(sorted(self.counters.items())),
            "requests": [dict(entry) for entry in self.requests],
        }


@dataclass(slots=True)
class RequestHead:
    method: str
    target: str
    version: str
    headers: list[tuple[str, str]]
    raw: bytes

    def header(self, name: str) -> str | None:
        lowered = name.lower()
        for key, value in self.headers:
            if key.lower() == lowered:
                return value
        return None

    @property
    def chunked(self) -> bool:
        return "chunked" in (self.header("transfer-encoding") or "").lower()

    @property
    def content_length(self) -> int | None:
        raw = self.header("content-length")
        if raw is None:
            return None
        try:
            return int(raw.strip())
        except ValueError as error:
            raise ProxyError(f"unparsable Content-Length: {raw!r}") from error


class BufferedReader:
    """A ``StreamReader`` view that first serves the bytes read past the request head.

    ``_read_head`` reads a block and keeps only the head, so the body bytes that
    arrived in the same TCP segment must be handed back to the body relay instead of
    being lost.
    """

    def __init__(self, reader: asyncio.StreamReader, residual: bytes = b"") -> None:
        self._reader = reader
        self._residual = bytearray(residual)

    def _take(self, limit: int) -> bytes:
        taken = bytes(self._residual[:limit])
        del self._residual[:len(taken)]
        return taken

    async def read(self, count: int = -1) -> bytes:
        if self._residual:
            return self._take(count if count >= 0 else len(self._residual))
        return await self._reader.read(count)

    async def readline(self) -> bytes:
        if self._residual:
            index = self._residual.find(b"\n")
            if index >= 0:
                return self._take(index + 1)
            buffered = bytes(self._residual)
            self._residual.clear()
            return buffered + await self._reader.readline()
        return await self._reader.readline()

    async def readexactly(self, count: int) -> bytes:
        if len(self._residual) >= count:
            return self._take(count)
        head = bytes(self._residual)
        self._residual.clear()
        return head + await self._reader.readexactly(count - len(head))


async def _read_head(reader: asyncio.StreamReader) -> tuple[RequestHead, bytes] | None:
    """One HTTP request head plus whatever body bytes arrived with it."""

    raw = bytearray()
    while b"\r\n\r\n" not in raw:
        chunk = await reader.read(4096)
        if not chunk:
            return None
        raw.extend(chunk)
        if len(raw) > MAX_HEAD_BYTES:
            raise ProxyError("request head exceeded the proxy's bound")
    head, _, residual = bytes(raw).partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    if not lines or not lines[0]:
        raise ProxyError("empty request line")
    parts = lines[0].decode("latin-1").split(" ")
    if len(parts) != 3:
        raise ProxyError(f"malformed request line: {lines[0]!r}")
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        name, sep, value = line.decode("latin-1").partition(":")
        if not sep:
            raise ProxyError(f"malformed header line: {line!r}")
        headers.append((name.strip(), value.strip()))
    return RequestHead(parts[0], parts[1], parts[2], headers, bytes(head)), residual


def _with_connection_close(head: RequestHead) -> bytes:
    """The request head, with the client's ``Connection`` replaced by ``close``.

    The proxy answers one request per connection, so the upstream is told the
    connection ends with this request; the Gateway then closes the response, which
    is how the relay knows it is complete without framing the response itself.
    """

    lines = [f"{head.method} {head.target} {head.version}"]
    for name, value in head.headers:
        if name.lower() == "connection":
            continue
        lines.append(f"{name}: {value}")
    lines.append("Connection: close")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


class _Sink:
    """The verbatim write side of the body relay."""

    def __init__(self, writer: Any) -> None:
        self._writer = writer

    def write(self, data: bytes) -> None:
        self._writer.write(data)

    async def drain(self) -> None:
        await self._writer.drain()


class _DiscardSink:
    """A sink that swallows bytes: the mid-body fault reads the body and forwards none."""

    def write(self, _data: bytes) -> None:
        return None

    async def drain(self) -> None:
        return None


async def _relay_body(
    reader: BufferedReader, sink: Any, head: RequestHead, *, mid_body_stop: int | None = None,
) -> tuple[int, bool]:
    """Relay a request body verbatim; return ``(bytes, complete)``.

    ``mid_body_stop`` relays at most that many bytes and reports ``complete=False``,
    which is the "connection died while the body was being written" fault.
    """

    if head.chunked:
        return await _relay_chunked(reader, sink, mid_body_stop=mid_body_stop)
    length = head.content_length
    if length is None:
        return 0, True
    relayed = 0
    while relayed < length:
        want = min(65536, length - relayed)
        if mid_body_stop is not None:
            remaining = mid_body_stop - relayed
            if remaining <= 0:
                return relayed, False
            want = min(want, remaining)
        chunk = await reader.read(want)
        if not chunk:
            return relayed, False
        sink.write(chunk)
        await sink.drain()
        relayed += len(chunk)
    return relayed, True


async def _relay_chunked(
    reader: BufferedReader, sink: Any, *, mid_body_stop: int | None = None,
) -> tuple[int, bool]:
    """Relay a chunked body verbatim, tracking where the body ends.

    The server streams its write bodies (an async generator), so httpx sends them
    ``Transfer-Encoding: chunked``: a drop "after the full body" has to recognise the
    terminating zero chunk, not a ``Content-Length``.
    """

    relayed = 0
    while True:
        line = await reader.readline()
        if not line:
            return relayed, False
        sink.write(line)
        await sink.drain()
        size_field = line.split(b";", 1)[0].strip()
        try:
            size = int(size_field, 16)
        except ValueError as error:
            raise ProxyError(f"malformed chunk size: {line!r}") from error
        if size == 0:
            while True:
                trailer = await reader.readline()
                if not trailer:
                    return relayed, True
                sink.write(trailer)
                await sink.drain()
                if trailer in (b"\r\n", b"\n"):
                    return relayed, True
        body = await reader.readexactly(size + 2)
        if mid_body_stop is not None and relayed + size > mid_body_stop:
            room = max(0, mid_body_stop - relayed)
            if room:
                sink.write(body[:room])
                await sink.drain()
            return relayed + room, False
        sink.write(body)
        await sink.drain()
        relayed += size


def _abort(writer: asyncio.StreamWriter) -> None:
    """Kill one connection with a RST, so the peer cannot read it as a clean close."""

    transport = writer.transport
    try:
        raw = transport.get_extra_info("socket")
        if raw is not None:
            raw.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    except (OSError, AttributeError):
        LOGGER.debug("could not set SO_LINGER on a dropped connection")
    transport.abort()


class FaultProxy:
    """The data listener, the control listener and the state they share."""

    def __init__(
        self, *, listen_host: str, listen_port: int, control_host: str, control_port: int,
        upstream_host: str, upstream_port: int,
    ) -> None:
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.control_host = control_host
        self.control_port = control_port
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.state = ProxyState()
        self.closed = asyncio.Event()
        self._data_server: asyncio.AbstractServer | None = None
        self._control_server: asyncio.AbstractServer | None = None

    # ------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        self._control_server = await asyncio.start_server(
            self._control_connection, self.control_host, self.control_port,
        )
        self.control_port = self._bound_port(self._control_server, self.control_port)
        await self._open_data_listener()
        LOGGER.info(
            "fault proxy: data %s:%s -> %s:%s, control %s:%s",
            self.listen_host, self.listen_port, self.upstream_host, self.upstream_port,
            self.control_host, self.control_port,
        )

    @property
    def control_address(self) -> str:
        return f"http://{self.control_host}:{self.control_port}"

    @staticmethod
    def _bound_port(server: asyncio.AbstractServer, configured: int) -> int:
        """The port a listener actually bound, so ``0`` can mean "any free port"."""

        if configured:
            return configured
        sockets = server.sockets or ()
        if not sockets:
            raise ProxyError("listener reported no socket")
        return int(sockets[0].getsockname()[1])

    async def _open_data_listener(self) -> None:
        if self._data_server is None:
            self._data_server = await asyncio.start_server(
                self._data_connection, self.listen_host, self.listen_port,
            )
            self.listen_port = self._bound_port(self._data_server, self.listen_port)
            self.state.listening = True

    async def _close_data_listener(self) -> None:
        server, self._data_server = self._data_server, None
        if server is not None:
            # ``server.close()`` is what stops accepting (and therefore what makes the next
            # connect a refused one). ``Server.wait_closed()`` is deliberately not awaited
            # here: since Python 3.12 it also waits for every accepted connection's handler
            # to finish, and this runs *inside* one of them — awaiting it would deadlock
            # against the very write this fault exists to refuse.
            server.close()
        self.state.listening = False

    async def close(self) -> None:
        await self._close_data_listener()
        if self._control_server is not None:
            server, self._control_server = self._control_server, None
            server.close()
            await server.wait_closed()

    def stop(self) -> None:
        self.closed.set()

    async def serve(self) -> None:
        await self.start()
        try:
            await self.closed.wait()
        finally:
            await self.close()

    # --------------------------------------------------------------- control

    async def _control_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        try:
            while True:
                parsed = await _read_head(reader)
                if parsed is None:
                    return
                head, residual = parsed
                buffered = BufferedReader(reader, residual)
                length = head.content_length or 0
                body = await buffered.readexactly(length) if length else b""
                status, payload = await self._control_route(head, body)
                encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n"
                writer.write(
                    f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
                    f"Content-Length: {len(encoded)}\r\nConnection: close\r\n\r\n".encode("latin-1")
                    + encoded
                )
                await writer.drain()
                return
        except (ProxyError, asyncio.IncompleteReadError) as error:
            LOGGER.warning("control channel rejected a request: %s", error)
        finally:
            writer.close()

    async def _control_route(
        self, head: RequestHead, body: bytes,
    ) -> tuple[str, dict[str, Any]]:
        target = head.target.split("?", 1)[0].rstrip("/") or "/"
        if head.method.upper() == "GET" and target in {"/", "/state"}:
            return "200 OK", self.state.snapshot()
        if head.method.upper() == "POST" and target == "/fault":
            return await self._arm(body)
        if head.method.upper() == "POST" and target == "/reset":
            self.state.counters.clear()
            self.state.requests.clear()
            return "200 OK", self.state.snapshot()
        return "404 Not Found", {"error": f"no control route for {head.method} {head.target}"}

    async def _arm(self, body: bytes) -> tuple[str, dict[str, Any]]:
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except ValueError:
            return "400 Bad Request", {"error": "the fault body was not JSON"}
        if not isinstance(payload, dict):
            return "400 Bad Request", {"error": "the fault body was not a JSON object"}
        mode = str(payload.get("mode", MODE_NONE))
        if mode not in MODES:
            return "400 Bad Request", {"error": f"unknown fault mode: {mode}", "modes": list(MODES)}
        self.state.armed = Fault(
            mode=mode,
            method=str(payload.get("method", "")),
            path_contains=str(payload.get("pathContains", "")),
            delay_seconds=float(payload.get("delaySeconds", 0.0)),
            mid_body_bytes=int(payload.get("midBodyBytes", DEFAULT_MID_BODY_BYTES)),
            after_count=int(payload.get("afterCount", 1)),
            one_shot=bool(payload.get("oneShot", True)),
        )
        if self.state.armed.mode == MODE_CONNECT_REFUSED:
            await self._close_data_listener()
        else:
            await self._open_data_listener()
        return "200 OK", self.state.snapshot()

    # ------------------------------------------------------------------ data

    async def _data_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        try:
            await self._serve_one_request(reader, writer)
        except (ProxyError, asyncio.IncompleteReadError, ConnectionError) as error:
            LOGGER.warning("data connection ended: %s", error)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - one request must not kill the proxy
            LOGGER.exception("data connection failed")
        finally:
            if not writer.is_closing():
                writer.close()

    def _select_fault(self, method: str, target: str) -> tuple[Fault | None, str | None]:
        """Which fault this request triggers, and what to record about it.

        ``refuse_after_forward`` counts matching requests first: every one before the
        trigger's ordinal is relayed untouched (labelled ``<mode>:pending``) and the
        trigger itself is relayed before the listener goes away.
        """

        armed = self.state.armed
        if armed.mode in (MODE_NONE, MODE_CONNECT_REFUSED) or not armed.matches(method, target):
            return None, None
        if armed.mode == MODE_REFUSE_AFTER_FORWARD:
            armed.seen += 1
            if armed.seen < armed.after_count:
                self.state.bump("refusals_pending")
                return None, f"{armed.mode}:pending"
            self.state.armed = Fault()
            return armed, armed.mode
        if armed.one_shot and armed.mode in ONE_SHOT_MODES:
            self.state.armed = Fault()
        return armed, armed.mode

    async def _serve_one_request(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        parsed = await _read_head(reader)
        if parsed is None:
            return
        head, residual = parsed
        buffered = BufferedReader(reader, residual)
        self.state.bump("client_requests")
        self.state.bump("requests_seen")
        # A per-method count is the exact "how many writes did this server attempt"
        # evidence a case compares before and after: the bounded request ring below is
        # for reading one entry back, not for counting.
        self.state.bump(f"method_{head.method.upper()}")
        entry: dict[str, Any] = {
            "seq": self.state.counters["requests_seen"],
            "method": head.method, "target": head.target, "fault": None, "bodyBytes": 0,
            "bodyComplete": False, "forwarded": False, "dropped": False, "responseBytes": 0,
        }
        self.state.record(entry)
        fault, label = self._select_fault(head.method, head.target)
        entry["fault"] = label
        if fault is None:
            await self._forward(buffered, writer, head, entry)
            return
        self.state.bump("matching_requests")
        self.state.bump(f"fault_{fault.mode}")
        if fault.mode == MODE_DROP_MID_BODY:
            await self._drop_mid_body(buffered, writer, head, fault, entry)
        elif fault.mode == MODE_DROP_AFTER_BODY:
            await self._drop_after_body(buffered, writer, head, entry)
        elif fault.mode == MODE_REFUSE_AFTER_FORWARD:
            await self._refuse_after_forward(buffered, writer, head, entry)
        else:
            await self._delay_response(buffered, writer, head, fault, entry)

    async def _refuse_after_forward(
        self, reader: BufferedReader, writer: asyncio.StreamWriter, head: RequestHead,
        entry: dict[str, Any],
    ) -> None:
        """Relay the trigger normally, then take the data listener away.

        The *next* connect — the write the Tool dispatches — is then a refused connect on
        a port the server has been using all along, which is what D08's ``NOT_SENT``
        boundary is made of.
        """

        await self._forward(reader, writer, head, entry)
        await self._close_data_listener()
        self.state.bump("listeners_closed")

    async def _connect_upstream(
        self, entry: dict[str, Any],
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            return await asyncio.open_connection(self.upstream_host, self.upstream_port)
        except OSError as error:
            self.state.bump("upstream_connect_failures")
            entry["upstreamError"] = type(error).__name__
            raise

    async def _drop_mid_body(
        self, reader: BufferedReader, writer: asyncio.StreamWriter, head: RequestHead,
        fault: Fault, entry: dict[str, Any],
    ) -> None:
        """Kill the connection mid-body, before any upstream dial.

        Nothing reaches the Gateway: the head was never forwarded, so this is a write
        that never left the client side of the hop.
        """

        self.state.bump("dropped_before_upstream")
        entry["dropped"] = True
        relayed, complete = await _relay_body(
            reader, _DiscardSink(), head, mid_body_stop=fault.mid_body_bytes,
        )
        entry["bodyBytes"] = relayed
        entry["bodyComplete"] = complete
        _abort(writer)

    async def _drop_after_body(
        self, reader: BufferedReader, writer: asyncio.StreamWriter, head: RequestHead,
        entry: dict[str, Any],
    ) -> None:
        """Send the whole request and read the answer, then kill the connection."""

        upstream_reader, upstream_writer = await self._connect_upstream(entry)
        try:
            upstream_writer.write(_with_connection_close(head))
            relayed, complete = await _relay_body(reader, _Sink(upstream_writer), head)
            entry["bodyBytes"] = relayed
            entry["bodyComplete"] = complete
            entry["forwarded"] = True
            self.state.bump("forwarded_to_upstream")
            _answer, size = await self._read_answer(upstream_reader)
            entry["responseBytes"] = size
        finally:
            _abort(upstream_writer)
        self.state.bump("dropped_after_full_body")
        entry["dropped"] = True
        _abort(writer)

    async def _delay_response(
        self, reader: BufferedReader, writer: asyncio.StreamWriter, head: RequestHead,
        fault: Fault, entry: dict[str, Any],
    ) -> None:
        """Forward the request normally and hold the answer back for the deadline."""

        upstream_reader, upstream_writer = await self._connect_upstream(entry)
        try:
            upstream_writer.write(_with_connection_close(head))
            relayed, complete = await _relay_body(reader, _Sink(upstream_writer), head)
            entry["bodyBytes"] = relayed
            entry["bodyComplete"] = complete
            entry["forwarded"] = True
            self.state.bump("forwarded_to_upstream")
            answer, size = await self._read_answer(upstream_reader)
            self.state.bump("delayed_responses")
            await asyncio.sleep(fault.delay_seconds)
            entry["responseBytes"] = size
            if answer and not writer.is_closing():
                writer.write(answer)
                await writer.drain()
        finally:
            _abort(upstream_writer)

    async def _forward(
        self, reader: BufferedReader, writer: asyncio.StreamWriter, head: RequestHead,
        entry: dict[str, Any],
    ) -> None:
        """The healthy path: relay the request, then relay the answer streaming."""

        upstream_reader, upstream_writer = await self._connect_upstream(entry)
        try:
            upstream_writer.write(_with_connection_close(head))
            relayed, complete = await _relay_body(reader, _Sink(upstream_writer), head)
            entry["bodyBytes"] = relayed
            entry["bodyComplete"] = complete
            entry["forwarded"] = True
            self.state.bump("forwarded_to_upstream")
            entry["responseBytes"] = await self._forward_answer(upstream_reader, writer)
            self.state.bump("completed_requests")
        finally:
            upstream_writer.close()

    async def _forward_answer(
        self, upstream_reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> int:
        """Stream the upstream answer, telling the client this hop is one-shot.

        The ``Connection: close`` the proxy sends upstream is not necessarily echoed, and
        a pooled HTTP client that does not learn the connection ends would send its *next*
        request into a socket this proxy has already closed — an ambiguous boundary where
        the fault armed here means a refused connect. Rewriting the answer head closes
        that hole, and the declared ``Content-Length`` keeps the relay from waiting on an
        upstream that never closes.
        """

        answer_head, residual = await self._read_answer_head(upstream_reader)
        core = _with_connection_close(answer_head)
        writer.write(core + residual)
        await writer.drain()
        delivered = len(core) + len(residual)
        declared = answer_head.content_length
        remaining = None if declared is None else max(0, declared - len(residual))
        while remaining is None or remaining > 0:
            want = 65536 if remaining is None else min(65536, remaining)
            chunk = await upstream_reader.read(want)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
            delivered += len(chunk)
            if remaining is not None:
                remaining -= len(chunk)
        return delivered

    async def _read_answer_head(
        self, upstream_reader: asyncio.StreamReader,
    ) -> tuple[RequestHead, bytes]:
        parsed = await _read_head(upstream_reader)
        if parsed is None:
            raise ProxyError("the upstream closed before it answered")
        return parsed

    async def _read_answer(
        self, upstream_reader: asyncio.StreamReader,
    ) -> tuple[bytes, int]:
        """One complete upstream answer, held in memory (the modes that drop or delay it)."""

        answer_head, residual = await self._read_answer_head(upstream_reader)
        body = bytearray(residual)
        declared = answer_head.content_length
        while len(body) < MAX_BUFFERED_RESPONSE_BYTES:
            if declared is not None and len(body) >= declared:
                break
            chunk = await upstream_reader.read(65536)
            if not chunk:
                break
            body.extend(chunk)
        core = _with_connection_close(answer_head)
        return core + bytes(body[:MAX_BUFFERED_RESPONSE_BYTES]), len(core) + len(body)


def _free_port(host: str) -> int:
    with socket.socket() as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--control-host", default="127.0.0.1")
    parser.add_argument("--control-port", type=int, default=0)
    parser.add_argument("--upstream-host", default="127.0.0.1")
    parser.add_argument("--upstream-port", type=int, required=True)
    parser.add_argument(
        "--state-file", default="",
        help="write the effective addresses here once both listeners are up",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    proxy = FaultProxy(
        listen_host=args.listen_host, listen_port=args.listen_port,
        control_host=args.control_host,
        control_port=args.control_port or _free_port(args.control_host),
        upstream_host=args.upstream_host, upstream_port=args.upstream_port,
    )

    async def run() -> None:
        loop = asyncio.get_running_loop()
        for name in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(name, proxy.stop)
        await proxy.start()
        if args.state_file:
            with open(args.state_file, "w", encoding="utf-8") as handle:
                json.dump({
                    "listen": f"{args.listen_host}:{args.listen_port}",
                    "control": f"{proxy.control_host}:{proxy.control_port}",
                    "upstream": f"{args.upstream_host}:{args.upstream_port}",
                }, handle)
        await proxy.closed.wait()
        await proxy.close()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:  # pragma: no cover - operator convenience
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
