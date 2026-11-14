import asyncio
import os
import uuid
from typing import Iterable, List, Optional, Protocol

from temporalio.api.common.v1 import Payload
from temporalio.converter import PayloadCodec

from common.redis_config import RedisConfig

ENCODING_METADATA_KEY = "temporal.io/claim-check-codec"
ENCODING_METADATA_VALUE = b"v1"


class ClaimCheckStore(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...

    async def get(self, key: str) -> Optional[bytes]: ...


class RedisClaimCheckStore:
    """Payloads in Redis, under their claim-check key (the local default)."""

    def __init__(self, config: RedisConfig):
        # TODO: async cleanup challenge — redis_client is not closed on shutdown
        self.redis_client = config.async_client(decode_responses=False)

    async def put(self, key: str, data: bytes) -> None:
        await self.redis_client.set(key, data)

    async def get(self, key: str) -> Optional[bytes]:
        return await self.redis_client.get(key)


class S3ClaimCheckStore:
    """Payloads in an S3 bucket, as objects <prefix><claim-check key>.

    Credentials and region come from the standard AWS environment (a pod's role, a
    profile, AWS_ENDPOINT_URL_S3 for an S3-compatible store such as MinIO).
    """

    def __init__(self, bucket: str, prefix: str = "claim-check/"):
        import boto3

        self.bucket = bucket
        self.prefix = prefix
        self.s3 = boto3.client("s3")

    async def put(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(self.s3.put_object, Bucket=self.bucket, Key=self.prefix + key, Body=data)

    async def get(self, key: str) -> Optional[bytes]:
        try:
            response = await asyncio.to_thread(self.s3.get_object, Bucket=self.bucket, Key=self.prefix + key)
        except self.s3.exceptions.NoSuchKey:
            return None
        return await asyncio.to_thread(response["Body"].read)


def claim_check_store_from_env() -> ClaimCheckStore:
    """S3 when CLAIM_CHECK_S3_BUCKET names a bucket, else Redis."""
    bucket = os.getenv("CLAIM_CHECK_S3_BUCKET")
    if bucket:
        return S3ClaimCheckStore(bucket)
    return RedisClaimCheckStore(RedisConfig())


class ClaimCheckCodec(PayloadCodec):
    """Temporal PayloadCodec implementing the claim check pattern.

    Replaces each payload with a UUID and keeps the payload itself in a store (S3 or
    Redis), keeping workflow event history small and the data out of Temporal.
    """

    def __init__(self, store: Optional[ClaimCheckStore] = None):
        self.store = store or claim_check_store_from_env()

    async def encode(self, payloads: Iterable[Payload]) -> List[Payload]:
        return [await self._encode_payload(p) for p in payloads]

    async def decode(self, payloads: Iterable[Payload]) -> List[Payload]:
        return [await self._decode_payload(p) for p in payloads]

    async def _encode_payload(self, payload: Payload) -> Payload:
        claim_key = str(uuid.uuid4())
        await self.store.put(claim_key, payload.SerializeToString())
        return Payload(
            metadata={
                "encoding": b"binary/claim-check",
                ENCODING_METADATA_KEY: ENCODING_METADATA_VALUE,
            },
            data=claim_key.encode(),
        )

    async def _decode_payload(self, payload: Payload) -> Payload:
        if payload.metadata.get(ENCODING_METADATA_KEY) != ENCODING_METADATA_VALUE:
            return payload
        claim_key = payload.data.decode()
        data = await self.store.get(claim_key)
        if data is None:
            raise ValueError(f"Claim check {claim_key} not found")
        result = Payload()
        result.ParseFromString(data)
        return result
