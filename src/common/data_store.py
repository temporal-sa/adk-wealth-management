"""Where the demo's sample bank data (data/*.json) is read from and written to.

DEMO_DATA_STORE=file (the default): the JSON files themselves, as when one person runs the
demo locally (git checkout data/ resets them).

DEMO_DATA_STORE=redis: one copy of each file per browser session, in Redis under
wm:<task queue>:<session>:data:<name>. A session's first read starts from the file in data/,
its writes never touch the file, and its copy expires a day after its last change. This is
what a hosted, multi-user deployment uses: everyone gets their own client 123 to play with,
and the data survives a worker restart.
"""

import json
import logging
import os
from typing import Any, Optional

from common.redis_config import RedisConfig
from common.session_scope import SESSION_TTL_SECONDS, current_activity_scope

logger = logging.getLogger(__name__)


def _use_redis() -> bool:
    return os.getenv("DEMO_DATA_STORE", "file").strip().lower() == "redis"


_redis_client = None


def _shared_redis():
    # One connection pool per process, shared by every activity
    global _redis_client
    if _redis_client is None:
        _redis_client = RedisConfig().sync_client()
    return _redis_client


class JsonDocument:
    """One JSON data file, as the current session sees it."""

    def __init__(self, name: str, file_path: str, scope: Optional[str] = None):
        self.name = name
        self.file_path = file_path
        self._redis = None
        if _use_redis():
            # The session of the workflow whose activity is running, unless given
            self._key = f"wm:{scope or current_activity_scope()}:data:{name}"
            self._redis = _shared_redis()

    def _read_file(self) -> Optional[Any]:
        if not os.path.exists(self.file_path) or os.stat(self.file_path).st_size == 0:
            return None
        with open(self.file_path, "r") as f:
            return json.load(f)

    def load(self) -> Optional[Any]:
        """The document, or None if it doesn't exist or is empty."""
        if self._redis is None:
            return self._read_file()
        raw = self._redis.get(self._key)
        if raw is None:
            return self._read_file()
        return json.loads(raw)

    def save(self, data: Any, indent: int = 4) -> None:
        if self._redis is None:
            with open(self.file_path, "w") as f:
                json.dump(data, f, indent=indent)
            return
        self._redis.set(self._key, json.dumps(data), ex=SESSION_TTL_SECONDS)
