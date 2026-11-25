"""cli._rpc returns the reply to its own request, not a notification the engine broadcast first."""
import asyncio
import json
import tempfile

from misty_engine.cli import _rpc


def test_rpc_skips_notifications():
    async def run():
        with tempfile.TemporaryDirectory(dir="/tmp") as d:   # short path: unix socket names are length-limited
            sock = d + "/s"

            async def serve(reader, writer):
                req = json.loads(await reader.readline())
                writer.write(json.dumps({"jsonrpc": "2.0", "method": "stream.status", "params": {}}).encode() + b"\n")
                writer.write(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": {"data": {"name": "Safari"}}}).encode() + b"\n")
                await writer.drain()
                writer.close()

            srv = await asyncio.start_unix_server(serve, sock)
            async with srv:
                return await _rpc(sock, "hands.call", {"op": "front_app"})

    r = asyncio.run(run())
    assert r["result"]["data"]["name"] == "Safari"
