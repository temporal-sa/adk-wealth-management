import uuid
from typing import Iterable, List

import redis.asyncio as aioredis
from temporalio.api.common.v1 import Payload
from temporalio.converter import PayloadCodec

from common.redis_config import RedisConfig

ENCODING_METADATA_KEY = "temporal.io/claim-check-codec"
ENCODING_METADATA_VALUE = b"v1"


class ClaimCheckCodec(PayloadCodec):
    """Temporal PayloadCodec implementing the claim check pattern.

    Replaces each payload with a UUID stored in Redis, keeping workflow
    event history small and enabling encryption-at-rest in Redis.
    """

    def __init__(self, config: RedisConfig):
        # TODO: async cleanup challenge — redis_client is not closed on shutdown
        self.redis_client = aioredis.Redis(host=config.hostname, port=config.port)

    async def encode(self, payloads: Iterable[Payload]) -> List[Payload]:
        return [await self._encode_payload(p) for p in payloads]

    async def decode(self, payloads: Iterable[Payload]) -> List[Payload]:
        return [await self._decode_payload(p) for p in payloads]

    async def _encode_payload(self, payload: Payload) -> Payload:
        claim_key = str(uuid.uuid4())
        await self.redis_client.set(claim_key, payload.SerializeToString())
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
        data = await self.redis_client.get(claim_key)
        result = Payload()
        result.ParseFromString(data)
        return result
