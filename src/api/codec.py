"""Codec endpoint for the Temporal UI: POST /codec/decode and /codec/encode.

Set the Temporal UI's codec server (Data Encoder) endpoint to https://<host>/codec to see
claim-checked payloads (USE_CLAIM_CHECK=true) in workflow histories. The UI calls it from
the browser, cross-origin, so CORS allows https://cloud.temporal.io, http://localhost:8233,
the platform's TEMPORAL_UI_URL and CODEC_CORS_ORIGINS.

CODEC_AUTH=jwt (the hosted setup) requires the Temporal Cloud UI's access token: turn on
"Pass the user access token" in the UI's codec settings. The token's signature is verified
against Temporal Cloud's JWKS (CODEC_JWKS_URL) and its audience (CODEC_JWT_AUDIENCE).
CODEC_AUTH=none (the default) checks nothing, for the local dev server's UI.

/decode resolves claim-check references (random UUIDs that only appear in the histories).
/encode returns payloads unchanged, so callers of this endpoint never write to the
claim-check store; the workers decode unencoded payloads as they are.
"""

import asyncio
import logging
import os
from typing import Optional
from urllib.parse import urlparse

import jwt
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from google.protobuf import json_format
from temporalio.api.common.v1 import Payloads
from temporalio.converter import PayloadCodec

logger = logging.getLogger(__name__)

TEMPORAL_CLOUD_JWKS_URL = "https://login.tmprl.cloud/.well-known/jwks.json"
TEMPORAL_CLOUD_JWT_AUDIENCE = "https://saas-api.tmprl.cloud"


def codec_cors_origins() -> list[str]:
    origins = os.getenv("CODEC_CORS_ORIGINS", "https://cloud.temporal.io,http://localhost:8233").split(",")
    ui_url = os.getenv("TEMPORAL_UI_URL")
    if ui_url:
        parsed = urlparse(ui_url)
        if parsed.scheme and parsed.netloc:
            origins.append(f"{parsed.scheme}://{parsed.netloc}")
    return sorted({o.strip().rstrip("/") for o in origins if o.strip()})


class TemporalCloudTokenVerifier:
    """Verifies a Temporal Cloud UI access token (RS256 JWT) against a JWKS."""

    def __init__(self, jwks_url: Optional[str] = None, audience: Optional[str] = None):
        self.jwks = jwt.PyJWKClient(jwks_url or os.getenv("CODEC_JWKS_URL", TEMPORAL_CLOUD_JWKS_URL), lifespan=3600)
        self.audience = audience or os.getenv("CODEC_JWT_AUDIENCE", TEMPORAL_CLOUD_JWT_AUDIENCE)

    def verify(self, token: str) -> dict:
        signing_key = self.jwks.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=self.audience,
            options={"require": ["exp"]},
        )


def build_codec_app(codec: PayloadCodec) -> FastAPI:
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=codec_cors_origins(),
        allow_credentials=True,
        allow_methods=["POST", "OPTIONS"],
        allow_headers=["content-type", "x-namespace", "authorization"],
    )

    auth_mode = os.getenv("CODEC_AUTH", "none").strip().lower()
    if auth_mode not in ("none", "jwt"):
        raise ValueError(f"CODEC_AUTH must be none or jwt, not {auth_mode!r}")
    verifier = TemporalCloudTokenVerifier() if auth_mode == "jwt" else None

    async def authorize(request: Request) -> None:
        if verifier is None:
            return
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise HTTPException(status_code=401, detail="A Temporal Cloud access token is required")
        try:
            await asyncio.to_thread(verifier.verify, token.strip())
        except Exception as e:
            logger.info(f"Codec request refused: {type(e).__name__}: {e}")
            raise HTTPException(status_code=401, detail="Invalid access token")

    async def read_payloads(request: Request) -> Payloads:
        try:
            return json_format.Parse(await request.body(), Payloads())
        except json_format.ParseError as e:
            raise HTTPException(status_code=400, detail=f"Invalid payloads: {e}")

    def to_response(payloads: Payloads) -> Response:
        return Response(content=json_format.MessageToJson(payloads), media_type="application/json")

    @app.post("/decode")
    async def decode(request: Request) -> Response:
        await authorize(request)
        payloads = await read_payloads(request)
        try:
            decoded = await codec.decode(payloads.payloads)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return to_response(Payloads(payloads=decoded))

    @app.post("/encode")
    async def encode(request: Request) -> Response:
        await authorize(request)
        # Unchanged: callers of this public endpoint never write to the claim-check store
        return to_response(await read_payloads(request))

    return app
