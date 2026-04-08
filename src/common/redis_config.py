import os
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class RedisConfig:
    hostname: str = "localhost"
    port: int = 6379

    def __post_init__(self):
        self.hostname = os.getenv("REDIS_HOST", self.hostname)
        self.port = int(os.getenv("REDIS_PORT", self.port))
