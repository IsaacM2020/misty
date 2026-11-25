"""Static guard: all HTTP goes through openrouter.py, Google libs only in the Google files."""
import ast
import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parents[2] / "engine" / "src" / "misty_engine"
HTTP_ROOTS = {"httpx", "requests", "aiohttp", "urllib3"}
HTTP_MODS = {"http.client", "urllib.request"}
ALLOW_HTTP = {"openrouter.py", "web.py", "local_llm.py"}   # web.py: chat's search and page reads (public web only, nothing secret out)
# local_llm.py: background jobs to the Mac Mini on Isaac's own Tailscale net (the url in the `local_llm` setting), never the internet
GOOGLE_FILES = {"google_auth.py", "gmail.py", "gcal.py"}


def _imports(path):
    for n in ast.walk(ast.parse(path.read_text())):
        if isinstance(n, ast.Import):
            yield from (a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            yield n.module


def test_only_openrouter_py_does_http():
    assert any(SRC.rglob("*.py"))
    for f in SRC.rglob("*.py"):
        for mod in _imports(f):
            root = mod.split(".")[0]
            if root in HTTP_ROOTS or mod in HTTP_MODS:
                assert f.name in ALLOW_HTTP, f"{f.name} imports {mod}"
            if root in {"googleapiclient", "google_auth_oauthlib", "google"}:
                assert f.name in GOOGLE_FILES, f"{f.name} imports {mod}"


def test_openrouter_host_only_in_client_and_config():
    for f in SRC.rglob("*.py"):
        if f.name in {"openrouter.py", "config.py"}:
            continue
        assert not re.search(r"openrouter\.ai", f.read_text(), re.I), f.name


def test_no_shell_http():
    for f in SRC.rglob("*.py"):
        assert not re.search(r"""["'](curl|wget)["']""", f.read_text()), f.name
