import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Set, List, Any, Optional

logger = logging.getLogger("eventora.realtime_bus")

class RealtimeEventBus:
    """
    High-performance, async Event Bus for real-time live synchronization.
    Supports Server-Sent Events (SSE) and WebSockets across 4,000-5,000 concurrent connections.
    """
    def __init__(self):
        # channel_name -> Set[asyncio.Queue]
        self._subscribers: Dict[str, Set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, channels: List[str]) -> asyncio.Queue:
        """Create a bounded queue and register it for the specified channels."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        async with self._lock:
            for ch in channels:
                clean_ch = ch.strip()
                if not clean_ch:
                    continue
                if clean_ch not in self._subscribers:
                    self._subscribers[clean_ch] = set()
                self._subscribers[clean_ch].add(queue)
        logger.debug(f"Subscribed queue to channels: {channels}. Total topics: {len(self._subscribers)}")
        return queue

    async def unsubscribe(self, queue: asyncio.Queue, channels: List[str]):
        """Remove the queue from specified channels."""
        async with self._lock:
            for ch in channels:
                clean_ch = ch.strip()
                if clean_ch in self._subscribers:
                    self._subscribers[clean_ch].discard(queue)
                    if not self._subscribers[clean_ch]:
                        del self._subscribers[clean_ch]
        logger.debug(f"Unsubscribed queue from channels: {channels}")

    def publish_sync(self, channel: str, event_type: str, data: Dict[str, Any]):
        """Publish an event synchronously without blocking the caller."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.publish(channel, event_type, data))
        except RuntimeError:
            pass

    async def publish(self, channel: str, event_type: str, data: Dict[str, Any]):
        """
        Publish an event to a channel and global listeners.
        Drops oldest message if subscriber queue is full, preventing lag or memory leaks.
        """
        payload = {
            "channel": channel,
            "event": event_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        targets: Set[asyncio.Queue] = set()
        async with self._lock:
            # Sockets listening directly to this channel
            if channel in self._subscribers:
                targets.update(self._subscribers[channel])
            # Sockets listening to global wildcard '*' or 'global'
            if "*" in self._subscribers:
                targets.update(self._subscribers["*"])
            if "global" in self._subscribers:
                targets.update(self._subscribers["global"])
            # Sockets listening to 'admin:all' if it's an admin event
            if channel.startswith("admin:") and "admin:all" in self._subscribers:
                targets.update(self._subscribers["admin:all"])

        if not targets:
            return

        for q in targets:
            if q.full():
                try:
                    q.get_nowait()  # Discard stale event to make room for newest
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass


# Global singleton
event_bus = RealtimeEventBus()
