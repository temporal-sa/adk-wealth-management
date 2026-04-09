import os
from collections.abc import Awaitable, Callable

from temporalio.client import Plugin, ClientConfig
from temporalio.converter import DataConverter
from temporalio.service import ConnectConfig, ServiceClient

from common.redis_config import RedisConfig
from common.util import str_to_bool
from temporal_supervisor.claim_check.claim_check_codec import ClaimCheckCodec


class ClaimCheckPlugin(Plugin):
    """Temporal client plugin that conditionally enables the claim check codec.

    Reads USE_CLAIM_CHECK from the environment. When true, all payloads are
    stored in Redis and replaced with UUIDs in the Temporal event history.
    """

    def __init__(self):
        self.use_claim_check = str_to_bool(os.getenv("USE_CLAIM_CHECK", "False"))
        self.redis_config = RedisConfig()

    def _get_data_converter(self, config: ClientConfig) -> DataConverter:
        default_converter_class = config["data_converter"].payload_converter_class
        if self.use_claim_check:
            print(f"Using claim check codec (USE_CLAIM_CHECK={self.use_claim_check})")
            return DataConverter(
                payload_converter_class=default_converter_class,
                payload_codec=ClaimCheckCodec(self.redis_config),
            )
        return DataConverter(payload_converter_class=default_converter_class)

    def configure_client(self, config: ClientConfig) -> ClientConfig:
        config["data_converter"] = self._get_data_converter(config)
        return config

    async def connect_service_client(
        self,
        config: ConnectConfig,
        next: Callable[[ConnectConfig], Awaitable[ServiceClient]],
    ) -> ServiceClient:
        return await next(config)
