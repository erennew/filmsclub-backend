"""Local check for JSONGZipMiddleware.

Exercises the middleware at the raw ASGI level. `starlette.testclient` (httpx)
silently decompresses gzip responses, so it cannot tell a real compressed body
from a plain one — these tests drive the ASGI callable directly and inspect the
bytes the server actually emits.

Covers: JSON compaction, the Accept-Encoding gate, the small-body threshold, the
streaming/non-/api exclusions, absence of duplicate Content-Type/Length, and
preservation of an existing Vary header.

Run with::

    .venv/Scripts/python.exe -m tests.test_json_gzip
"""

import asyncio
import gzip
import json
import sys
import unittest

sys.path.insert(0, ".")

from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from Backend.fastapi.main import JSONGZipMiddleware


def payload(rows: int = 200):
    return {"movies": [
        {"tmdb_id": i, "title": f"Film {i}", "description": "x" * 400}
        for i in range(rows)
    ]}


async def _json_endpoint(request):
    return JSONResponse(payload())


async def _small_endpoint(request):
    return JSONResponse({"ok": True})


async def _stream_endpoint(request):
    async def gen():
        for _ in range(50):
            yield b"0123456789" * 1000

    return StreamingResponse(gen(), media_type="video/mp4")


async def _html_endpoint(request):
    return Response("<html>" + "x" * 5000 + "</html>", media_type="text/html")


class AsgiResult:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers          # list[(name, value)] lowercased names
        self.body = body

    def header(self, name):
        values = [v for k, v in self.headers if k == name.lower()]
        return values[0] if values else None

    def header_values(self, name):
        return [v for k, v in self.headers if k == name.lower()]


async def call(app, path, method="GET", accept="gzip", extra_headers=None):
    """Drive the ASGI app and capture status, headers and the exact body bytes."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "headers": [],
    }
    if accept is not None:
        scope["headers"].append((b"accept-encoding", accept.encode()))
    for k, v in (extra_headers or {}).items():
        scope["headers"].append((k.lower().encode(), v.encode()))

    status = None
    headers = []
    body = bytearray()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        nonlocal status, headers
        if message["type"] == "http.response.start":
            status = message["status"]
            headers = [(k.decode().lower(), v.decode()) for k, v in message.get("headers", [])]
        elif message["type"] == "http.response.body":
            body.extend(message.get("body", b""))

    await app(scope, receive, send)
    return AsgiResult(status, headers, bytes(body))


def build_app(extra_middleware=None):
    app = Starlette(routes=[
        Route("/api/big", _json_endpoint),
        Route("/api/small", _small_endpoint),
        Route("/api/stream/abc", _stream_endpoint),
        Route("/admin/panel", _html_endpoint),
    ])
    if extra_middleware is not None:
        app.add_middleware(extra_middleware)
    app.add_middleware(JSONGZipMiddleware)
    return app


class JSONGZipTests(unittest.TestCase):
    def setUp(self):
        self.app = build_app()

    def get(self, path, **kwargs):
        return asyncio.run(call(self.app, path, **kwargs))

    def test_large_json_is_gzipped_and_round_trips(self):
        result = self.get("/api/big")
        self.assertEqual(result.status, 200)
        self.assertEqual(result.header("content-encoding"), "gzip")
        # Raw bytes must actually be a gzip stream that decompresses to the JSON.
        decoded = json.loads(gzip.decompress(result.body))
        self.assertEqual(decoded["movies"][0]["tmdb_id"], 0)
        self.assertEqual(len(decoded["movies"]), 200)

    def test_compression_ratio(self):
        plain = self.get("/api/big", accept="identity")
        zipped = self.get("/api/big")
        self.assertEqual(zipped.header("content-encoding"), "gzip")
        ratio = len(zipped.body) / len(plain.body)
        # Repetitive JSON should compress far below half its original size.
        self.assertLess(ratio, 0.5, f"compression ratio too weak: {ratio:.3f}")

    def test_client_without_gzip_support_gets_plain_body(self):
        result = self.get("/api/big", accept="identity")
        self.assertEqual(result.status, 200)
        self.assertIsNone(result.header("content-encoding"))
        self.assertEqual(json.loads(result.body)["movies"][0]["tmdb_id"], 0)

    def test_small_json_is_not_gzipped(self):
        result = self.get("/api/small")
        self.assertEqual(result.status, 200)
        self.assertIsNone(result.header("content-encoding"))
        self.assertEqual(json.loads(result.body), {"ok": True})

    def test_streaming_media_is_never_compressed(self):
        # Compare against the same stream fetched without gzip in the request:
        # the bytes must be identical, i.e. nothing was buffered or re-encoded.
        plain = self.get("/api/stream/abc", accept=None)
        gzipped = self.get("/api/stream/abc")
        self.assertEqual(gzipped.status, 200)
        self.assertIsNone(gzipped.header("content-encoding"))
        self.assertEqual(gzipped.body, plain.body)
        self.assertGreater(len(gzipped.body), 0)

    def test_non_api_html_is_untouched(self):
        result = self.get("/admin/panel")
        self.assertEqual(result.status, 200)
        self.assertIsNone(result.header("content-encoding"))
        self.assertTrue(result.body.startswith(b"<html>"))

    def test_ranged_request_is_never_compressed(self):
        # A client asking for a byte range is reading media; gzipping would
        # corrupt the offsets it expects.
        result = self.get("/api/big", extra_headers={"Range": "bytes=0-1023"})
        self.assertIsNone(result.header("content-encoding"))

    def test_no_duplicate_content_type_or_length(self):
        result = self.get("/api/big")
        self.assertEqual(len(result.header_values("content-type")), 1)
        self.assertEqual(len(result.header_values("content-length")), 1)

    def test_content_length_matches_the_sent_bytes(self):
        result = self.get("/api/big")
        self.assertEqual(int(result.header("content-length")), len(result.body))

    def test_vary_keeps_existing_value_and_adds_accept_encoding(self):
        # An inner middleware sets Vary: Origin (as CORSMiddleware does); the
        # gzip layer must extend that header rather than replace it.
        class SetOriginVary:
            def __init__(self, app):
                self.app = app

            async def __call__(self, scope, receive, send):
                if scope["type"] != "http":
                    return await self.app(scope, receive, send)

                async def wrapped(message):
                    if message["type"] == "http.response.start":
                        headers = list(message.get("headers", []))
                        if not any(k.lower() == b"vary" for k, _ in headers):
                            headers.append((b"vary", b"Origin"))
                        message = {**message, "headers": headers}
                    await send(message)

                await self.app(scope, receive, wrapped)

        app = Starlette(routes=[Route("/api/big", _json_endpoint)])
        app.add_middleware(SetOriginVary)          # added first -> inner
        app.add_middleware(JSONGZipMiddleware)     # added last  -> outer
        result = asyncio.run(call(app, "/api/big"))
        self.assertEqual(result.header("content-encoding"), "gzip")
        vary = {v.strip() for v in (result.header("vary") or "").split(",")}
        self.assertIn("Origin", vary)
        self.assertIn("Accept-Encoding", vary)


if __name__ == "__main__":
    unittest.main()
