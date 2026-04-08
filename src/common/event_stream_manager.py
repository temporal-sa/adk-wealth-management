import json
import time
import os
from typing import List, Dict, Any, Union
from enum import Enum
from dataclasses import asdict

import redis.asyncio as redis

from common.user_message import ChatInteraction
from common.status_update import StatusUpdate

class EventType(str, Enum):
    CHAT_INTERACTION = "chat_interaction"
    STATUS_UPDATE = "status_update"

class EventStreamManager:
    def __init__(self, redis_host: str = None, redis_port: int = None):
        self.redis_host = redis_host or os.getenv("REDIS_HOST", "localhost")
        self.redis_port = redis_port or int(os.getenv("REDIS_PORT", "6379"))
        self.redis_client = redis.Redis(
            host=self.redis_host,
            port=self.redis_port,
            decode_responses=True
        )

    def _get_stream_key(self, workflow_id: str) -> str:
        return f"events:{workflow_id}"

    def _get_meta_key(self, workflow_id: str) -> str:
        return f"events:{workflow_id}:meta"

    async def append_chat_interaction(self, workflow_id: str, chat_interaction: ChatInteraction) -> int:
        return await self._append_domain_event(workflow_id, EventType.CHAT_INTERACTION, chat_interaction)

    async def append_status_update(self, workflow_id: str, status_update: StatusUpdate) -> int:
        return await self._append_domain_event(workflow_id, EventType.STATUS_UPDATE, status_update)

    async def _append_domain_event(self, workflow_id: str, event_type: EventType, domain_object: Union[ChatInteraction, StatusUpdate]) -> int:
        stream_key = self._get_stream_key(workflow_id)
        content_dict = asdict(domain_object)
        event = {"type": event_type.value, "content": content_dict}
        event_json = json.dumps(event)
        new_length = await self.redis_client.rpush(stream_key, event_json)
        return new_length

    async def get_events_from_index(self, workflow_id: str, from_index: int = 0) -> List[Dict[str, Any]]:
        stream_key = self._get_stream_key(workflow_id)
        event_strings = await self.redis_client.lrange(stream_key, from_index, -1)
        events = []
        for event_str in event_strings:
            try:
                events.append(json.loads(event_str))
            except json.JSONDecodeError:
                continue
        return events

    async def get_all_events(self, workflow_id: str) -> List[Dict[str, Any]]:
        stream_key = self._get_stream_key(workflow_id)
        event_strings = await self.redis_client.lrange(stream_key, 0, -1)
        events = []
        for event_str in event_strings:
            try:
                events.append(json.loads(event_str))
            except json.JSONDecodeError:
                continue
        return events

    async def get_total_events(self, workflow_id: str) -> int:
        stream_key = self._get_stream_key(workflow_id)
        return await self.redis_client.llen(stream_key)

    async def delete_stream(self, workflow_id: str) -> bool:
        stream_key = self._get_stream_key(workflow_id)
        meta_key = self._get_meta_key(workflow_id)
        deleted = await self.redis_client.delete(stream_key, meta_key)
        return deleted > 0

    async def close(self):
        await self.redis_client.aclose()
