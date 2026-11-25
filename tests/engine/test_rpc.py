import asyncio
import json
import os
import stat

from misty_engine.errors import Fatal
from misty_engine.rpc import RpcServer


async def _echo(p):
    return {"got": p}


async def _boom(p):
    raise Fatal("KEY_INVALID")


async def test_handle_line_cases(short_tmp):
    s = RpcServer(short_tmp / "e.sock", {"echo": _echo, "boom": _boom})
    ok = await s.handle_line(b'{"jsonrpc":"2.0","id":1,"method":"echo","params":{"a":1}}')
    assert ok == {"jsonrpc": "2.0", "id": 1, "result": {"got": {"a": 1}}}
    assert (await s.handle_line(b"not json"))["error"]["code"] == -32700
    assert (await s.handle_line(b'{"jsonrpc":"2.0","id":2,"method":"nope"}'))["error"]["code"] == -32601
    e = await s.handle_line(b'{"jsonrpc":"2.0","id":3,"method":"boom"}')
    assert e["error"] == {"code": -32000, "message": "KEY_INVALID"}
    assert await s.handle_line(b'{"jsonrpc":"2.0","method":"echo"}') is None  # notification


async def test_socket_mode_0600_and_roundtrip(short_tmp):
    p = short_tmp / "e.sock"
    s = RpcServer(p, {"echo": _echo})
    await s.start()
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    r, w = await asyncio.open_unix_connection(str(p))
    w.write(b'{"jsonrpc":"2.0","id":7,"method":"echo","params":{}}\n')
    await w.drain()
    assert json.loads(await r.readline())["id"] == 7
    await s.notify("status.changed", {"online": True})
    assert json.loads(await r.readline())["method"] == "status.changed"
    w.close()
    await s.stop()
    assert not p.exists()
