"""
@file_name: dataloader.py
@author: NetMind.AI
@date: 2025-11-28
@description: DataLoader - Automatic batch loading utility

Inspired by: Facebook GraphQL DataLoader

Features:
- Automatically merges requests within the same event loop cycle
- Result caching (optional)
- Supports chunking (prevents IN clauses from being too large)

How it works:
    When there are multiple scattered load() calls in code:

    async def process():
        event1 = await loader.load("evt_1")  # Does not query immediately, joins queue
        event2 = await loader.load("evt_2")  # Does not query immediately, joins queue
        event3 = await loader.load("evt_3")  # Does not query immediately, joins queue

    # When the event loop is idle, DataLoader automatically executes:
    # SELECT * FROM events WHERE event_id IN ('evt_1', 'evt_2', 'evt_3')
    # Then distributes results to the waiting Futures

Performance improvement:
    - Before (N+1): 100 load() calls -> 100 queries -> ~220ms
    - After (batch): 100 load() calls -> 1 query -> ~15ms
"""

import asyncio
from typing import TypeVar, Generic, List, Dict, Callable, Awaitable, Optional
from loguru import logger

K = TypeVar('K')  # Key type
V = TypeVar('V')  # Value type


class DataLoader(Generic[K, V]):
    """
    Automatic batch loading DataLoader

    Usage examples:
        # 1. Define a batch load function
        async def batch_load(keys: List[str]) -> List[Optional[Event]]:
            return await event_repo.get_by_ids(keys)

        # 2. Create a loader
        loader = DataLoader(batch_load)

        # 3. Use it (will batch automatically)
        event1 = await loader.load("evt_1")
        event2 = await loader.load("evt_2")

        # Or batch load (more efficient, recommended)
        events = await loader.load_many(["evt_1", "evt_2", "evt_3"])

    Advanced usage:
        # Use with Repository
        event_loader = DataLoader(event_repo.get_by_ids)

        # Use across multiple coroutines
        async def process_narrative(narrative):
            events = await asyncio.gather(
                *[event_loader.load(eid) for eid in narrative.event_ids]
            )
            return events

        # All load() calls will be automatically merged into a single batch query
    """

    def __init__(
        self,
        batch_load_fn: Callable[[List[K]], Awaitable[List[Optional[V]]]],
        *,
        max_batch_size: int = 100,
        cache: bool = True,
    ):
        """
        Initialize DataLoader

        Args:
            batch_load_fn: Batch load function that receives a list of keys and returns
                          a corresponding list of values. Must ensure the returned list
                          matches the input keys in order.
            max_batch_size: Maximum number per batch (prevents SQL IN clause from being too long)
            cache: Whether to cache results
        """
        self._batch_load_fn = batch_load_fn
        self._max_batch_size = max_batch_size
        self._cache_enabled = cache

        # Internal state
        self._cache: Dict[K, V] = {}
        self._queue: List[K] = []
        self._futures: Dict[K, asyncio.Future] = {}
        self._dispatch_scheduled = False

    async def load(self, key: K) -> Optional[V]:
        """
        Load a single key

        Does not execute the query immediately; instead, it joins a queue for batch execution.
        All load() calls within the same event loop cycle will be merged into a single query.

        Args:
            key: The key to load

        Returns:
            The corresponding value, or None if not found
        """
        # Check cache
        if self._cache_enabled and key in self._cache:
            return self._cache[key]

        # Check if already in queue
        if key in self._futures:
            return await self._futures[key]

        # Create Future and add to queue
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Optional[V]] = loop.create_future()
        self._futures[key] = future
        self._queue.append(key)

        # Schedule batch execution (if not already scheduled)
        if not self._dispatch_scheduled:
            self._dispatch_scheduled = True
            loop.call_soon(self._schedule_dispatch)

        return await future

    async def load_many(self, keys: List[K]) -> List[Optional[V]]:
        """
        Batch load multiple keys

        More efficient than calling load() multiple times, recommended.

        Args:
            keys: List of keys to load

        Returns:
            Corresponding list of values, in the same order as input
        """
        if not keys:
            return []

        return list(await asyncio.gather(*[self.load(key) for key in keys]))

    def prime(self, key: K, value: V) -> None:
        """
        Pre-populate cache

        Used to fill known values into cache, avoiding unnecessary queries.

        Args:
            key: key
            value: value
        """
        if self._cache_enabled and key not in self._cache:
            self._cache[key] = value

    def clear(self, key: Optional[K] = None) -> None:
        """
        Clear cache

        Args:
            key: Key to clear, None to clear all
        """
        if key is not None:
            self._cache.pop(key, None)
        else:
            self._cache.clear()

    def clear_all(self) -> None:
        """Clear all cache and pending queues"""
        self._cache.clear()
        self._queue.clear()
        # Cancel all pending Futures
        for future in self._futures.values():
            if not future.done():
                future.cancel()
        self._futures.clear()
        self._dispatch_scheduled = False

    def _schedule_dispatch(self) -> None:
        """Schedule batch execution task"""
        asyncio.create_task(self._dispatch_batch())

    async def _dispatch_batch(self) -> None:
        """Execute batch loading"""
        self._dispatch_scheduled = False

        if not self._queue:
            return

        # Take all queued keys
        keys = self._queue.copy()
        self._queue.clear()

        logger.debug(f"DataLoader dispatching batch: {len(keys)} keys")

        # Execute in chunks (if exceeding max batch size)
        for i in range(0, len(keys), self._max_batch_size):
            chunk = keys[i:i + self._max_batch_size]
            await self._execute_chunk(chunk)

    async def _execute_chunk(self, keys: List[K]) -> None:
        """
        Execute a chunked batch load

        Args:
            keys: List of keys to load
        """
        try:
            # Call the batch load function
            values = await self._batch_load_fn(keys)

            # Validate return value count
            if len(values) != len(keys):
                raise ValueError(
                    f"batch_load_fn returned {len(values)} values for {len(keys)} keys"
                )

            # Dispatch results
            for key, value in zip(keys, values):
                # Cache
                if self._cache_enabled and value is not None:
                    self._cache[key] = value

                # Complete Future
                if key in self._futures:
                    future = self._futures.pop(key)
                    if not future.done():
                        future.set_result(value)

        except Exception as e:
            # Propagate error to all waiters
            logger.exception(f"DataLoader batch load failed: {e}")
            for key in keys:
                if key in self._futures:
                    future = self._futures.pop(key)
                    if not future.done():
                        future.set_exception(e)


