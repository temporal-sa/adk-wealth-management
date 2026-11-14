"""The /codec endpoint the Temporal UI decodes claim-checked payloads with (api/codec.py).

A local RSA key, served as a JWKS on 127.0.0.1, stands in for Temporal Cloud's; the
claim-check store is in memory. No network, Redis or AWS needed.
"""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from google.protobuf import json_format
from temporalio.api.common.v1 import Payload, Payloads

from api.codec import TEMPORAL_CLOUD_JWT_AUDIENCE, build_codec_app
from temporal_supervisor.claim_check.claim_check_codec import ClaimCheckCodec

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class MemoryStore:
    def __init__(self):
        self.items = {}

    async def put(self, key, data):
        self.items[key] = data

    async def get(self, key):
        return self.items.get(key)


@pytest.fixture(scope="module")
def jwks_url():
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key()))
    body = json.dumps({"keys": [{**jwk, "kid": "test-key", "use": "sig", "alg": "RS256"}]}).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/jwks.json"
    server.shutdown()


def token(key=KEY, audience=TEMPORAL_CLOUD_JWT_AUDIENCE, expires_in=300):
    claims = {"sub": "user", "aud": audience, "exp": int(time.time()) + expires_in}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})


@pytest.fixture
def codec():
    return ClaimCheckCodec(store=MemoryStore())


@pytest.fixture
def client(monkeypatch, jwks_url, codec):
    monkeypatch.setenv("CODEC_AUTH", "jwt")
    monkeypatch.setenv("CODEC_JWKS_URL", jwks_url)
    return TestClient(build_codec_app(codec))


def body(payloads):
    return json_format.MessageToJson(Payloads(payloads=payloads))


def claim_checked(codec, data=b'"hello"'):
    original = Payload(metadata={"encoding": b"json/plain"}, data=data)
    return original, asyncio.run(codec.encode([original]))[0]


def test_decode_resolves_claim_checks_for_a_valid_token(client, codec):
    original, encoded = claim_checked(codec)
    assert encoded.data != original.data
    response = client.post(
        "/decode", content=body([encoded]), headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"}
    )
    assert response.status_code == 200
    assert json_format.Parse(response.text, Payloads()).payloads[0] == original


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer not-a-jwt"},
        {"Authorization": f"Bearer {token(key=OTHER_KEY)}"},
        {"Authorization": f"Bearer {token(audience='https://elsewhere.example')}"},
        {"Authorization": f"Bearer {token(expires_in=-60)}"},
    ],
    ids=["no token", "garbage", "wrong key", "wrong audience", "expired"],
)
def test_decode_refuses_without_a_valid_token(client, codec, headers):
    _, encoded = claim_checked(codec)
    response = client.post("/decode", content=body([encoded]), headers={**headers, "Content-Type": "application/json"})
    assert response.status_code == 401


def test_encode_returns_payloads_unchanged_and_stores_nothing(client, codec):
    original = Payload(metadata={"encoding": b"json/plain"}, data=b'"from the UI"')
    response = client.post(
        "/encode", content=body([original]), headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"}
    )
    assert response.status_code == 200
    assert json_format.Parse(response.text, Payloads()).payloads[0] == original
    assert codec.store.items == {}


def test_cors_preflight_allows_the_temporal_cloud_ui(client):
    response = client.options(
        "/decode",
        headers={
            "Origin": "https://cloud.temporal.io",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,x-namespace",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://cloud.temporal.io"


def test_no_auth_mode_decodes_without_a_token(monkeypatch, codec):
    monkeypatch.setenv("CODEC_AUTH", "none")
    original, encoded = claim_checked(codec)
    response = TestClient(build_codec_app(codec)).post(
        "/decode", content=body([encoded]), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 200
    assert json_format.Parse(response.text, Payloads()).payloads[0] == original
