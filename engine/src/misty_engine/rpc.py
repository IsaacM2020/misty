"""JSON-RPC 2.0 over a Unix socket, one JSON object per line."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

from .errors import MistyError
from .logs import log_event

Method = Callable[[dict], Awaitable[Any]]


def _err(id_: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


class RpcServer:
    def __init__(self, sock_path: Path, methods: dict[str, Method]):
        self.sock_path = Path(sock_path)
        self.methods = methods
        self._server: asyncio.AbstractServer | None = None
        self._clients: set[asyncio.StreamWriter] = set()

    async def handle_line(self, line: bytes) -> dict | None:
        try:
            obj = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            return _err(None, -32700, "PARSE_ERROR")
        if not isinstance(obj, dict) or not isinstance(obj.get("method"), str):
            return _err(obj.get("id") if isinstance(obj, dict) else None, -32600, "INVALID_REQUEST")
        notification = "id" not in obj
        id_, method = obj.get("id"), obj["method"]
        fn = self.methods.get(method)
        if fn is None:
            return None if notification else _err(id_, -32601, "METHOD_NOT_FOUND")
        params = obj.get("params") or {}
        if not isinstance(params, dict):
            return None if notification else _err(id_, -32602, "INVALID_PARAMS")
        try:
            result = await fn(params)
        except MistyError as e:
            return None if notification else _err(id_, -32000, e.code)
        except Exception as e:  # never leak content, log the class only
            try:
                log_event("RPC_INTERNAL", method=method, exc=type(e).__name__)
            except Exception:
                pass
            return None if notification else _err(id_, -32603, "INTERNAL")
        return None if notification else {"jsonrpc": "2.0", "id": id_, "result": result}

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._clients.add(writer)
        inflight: set[asyncio.Task] = set()

        async def one(line: bytes) -> None:
            resp = await self.handle_line(line)
            if resp is not None:
                try:
                    writer.write(json.dumps(resp).encode() + b"\n")
                    await writer.drain()
                except (ConnectionError, RuntimeError):
                    pass                                  # the app went away mid-request: the work still finished

        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                if not line.strip():
                    continue
                # Each request runs on its own: a long chat.send or suggestions.go must not block the hands.result
                # (or chat.answer) that it is waiting for on the same connection. Replies carry their id.
                t = asyncio.create_task(one(line))
                inflight.add(t)
                t.add_done_callback(inflight.discard)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self._clients.discard(writer)
            writer.close()

    async def start(self) -> None:
        self.sock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.sock_path.exists():
            self.sock_path.unlink()
        old = os.umask(0o177)
        try:
            self._server = await asyncio.start_unix_server(self._on_client, path=str(self.sock_path), limit=16 * 1024 * 1024)  # a screenshot in hands.result is one long line
        finally:
            os.umask(old)
        os.chmod(self.sock_path, 0o600)

    async def notify(self, method: str, params: dict) -> None:
        msg = json.dumps({"jsonrpc": "2.0", "method": method, "params": params}).encode() + b"\n"
        for w in list(self._clients):
            try:
                w.write(msg)
                await w.drain()
            except ConnectionError:
                self._clients.discard(w)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        for w in list(self._clients):
            w.close()
        self._clients.clear()
        if self.sock_path.exists():
            self.sock_path.unlink()
