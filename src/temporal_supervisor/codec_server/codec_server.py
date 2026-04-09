"""Codec server for decoding claim-checked Temporal payloads in the Web UI.

Exposes POST /encode and POST /decode endpoints that the Temporal Web UI
(default: http://localhost:8233) can use to inspect workflow event history.

Usage:
    uv run python -m temporal_supervisor.codec_server.codec_server
"""

from functools import partial
from typing import Awaitable, Callable, Iterable, List

from aiohttp import hdrs, web
from google.protobuf import json_format
from temporalio.api.common.v1 import Payload, Payloads

from common.redis_config import RedisConfig
from temporal_supervisor.claim_check.claim_check_codec import ClaimCheckCodec

TEMPORAL_UI_ORIGIN = "http://localhost:8233"


def build_codec_server() -> web.Application:
    async def cors_options(req: web.Request) -> web.Response:
        resp = web.Response()
        if req.headers.get(hdrs.ORIGIN) == TEMPORAL_UI_ORIGIN:
            resp.headers[hdrs.ACCESS_CONTROL_ALLOW_ORIGIN] = TEMPORAL_UI_ORIGIN
            resp.headers[hdrs.ACCESS_CONTROL_ALLOW_METHODS] = "POST"
            resp.headers[hdrs.ACCESS_CONTROL_ALLOW_HEADERS] = "content-type,x-namespace"
        return resp

    async def apply(
        fn: Callable[[Iterable[Payload]], Awaitable[List[Payload]]],
        req: web.Request,
    ) -> web.Response:
        assert req.content_type == "application/json"
        data = await req.read()
        payloads = json_format.Parse(data, Payloads())
        payloads = Payloads(payloads=await fn(payloads.payloads))
        resp = await cors_options(req)
        resp.content_type = "application/json"
        resp.text = json_format.MessageToJson(payloads)
        return resp

    codec = ClaimCheckCodec(config=RedisConfig())
    app = web.Application()
    app.add_routes(
        [
            web.post("/encode", partial(apply, codec.encode)),
            web.post("/decode", partial(apply, codec.decode)),
            web.options("/decode", cors_options),
        ]
    )
    return app


if __name__ == "__main__":
    web.run_app(build_codec_server(), host="127.0.0.1", port=8081)
