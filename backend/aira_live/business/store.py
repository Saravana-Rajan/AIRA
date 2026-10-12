"""Tiny transactional store. MemoryStore for unit tests, FirestoreStore for production/emulator."""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from typing import Awaitable, Callable, Protocol, TypeVar

T = TypeVar("T")


class Txn(Protocol):
    async def get(self, path: str) -> dict | None: ...
    def set(self, path: str, data: dict) -> None: ...


class Store(Protocol):
    async def run(self, fn: Callable[[Txn], Awaitable[T]]) -> T: ...
    async def get(self, path: str) -> dict | None: ...
    async def set(self, path: str, data: dict) -> None: ...
    async def list(self, collection_path: str) -> list[tuple[str, dict]]: ...
    async def find_nearest(self, collection_path: str, field: str, query: list[float], *, limit: int = 3,
                           threshold: float | None = None) -> list[tuple[str, dict, float]]: ...
    def server_ts(self) -> object: ...


class _MemTxn:
    def __init__(self, data: dict[str, dict]):
        self._data = data
        self.writes: dict[str, dict] = {}

    async def get(self, path: str) -> dict | None:
        if path in self.writes:
            return copy.deepcopy(self.writes[path])
        value = self._data.get(path)
        return copy.deepcopy(value) if value is not None else None

    def set(self, path: str, data: dict) -> None:
        self.writes[path] = copy.deepcopy(data)


class MemoryStore:
    """Serialised, all-or-nothing transactions over a dict of path -> document."""

    def __init__(self) -> None:
        self.data: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def run(self, fn):
        async with self._lock:
            txn = _MemTxn(self.data)
            result = await fn(txn)          # an exception discards txn.writes
            self.data.update(txn.writes)
            return result

    async def get(self, path: str) -> dict | None:
        value = self.data.get(path)
        return copy.deepcopy(value) if value is not None else None

    async def set(self, path: str, data: dict) -> None:
        self.data[path] = copy.deepcopy(data)

    async def list(self, collection_path: str) -> list[tuple[str, dict]]:
        prefix = collection_path.rstrip("/") + "/"
        out = []
        for path, doc in self.data.items():
            rest = path[len(prefix):] if path.startswith(prefix) else None
            if rest and "/" not in rest:
                out.append((rest, copy.deepcopy(doc)))
        return sorted(out, key=lambda x: x[0])

    async def find_nearest(self, collection_path: str, field: str, query: list[float], *, limit: int = 3,
                           threshold: float | None = None) -> list[tuple[str, dict, float]]:
        """Cosine ``find_nearest`` computed locally: (id, doc, distance), nearest first."""
        from aira_live.embeddings import nearest_local

        return nearest_local(await self.list(collection_path), query, field=field, limit=limit, threshold=threshold)

    def server_ts(self) -> object:
        return datetime.now(timezone.utc)


class _FsTxn:
    def __init__(self, client, transaction):
        self._c = client
        self._t = transaction

    async def get(self, path: str) -> dict | None:
        snap = await self._c.document(path).get(transaction=self._t)
        return snap.to_dict() if snap.exists else None

    def set(self, path: str, data: dict) -> None:
        self._t.set(self._c.document(path), data)


class FirestoreStore:
    """Firestore AsyncClient store (database "aira" via aira_live.db.db()). Transaction API:
    https://cloud.google.com/python/docs/reference/firestore/latest/google.cloud.firestore_v1.async_transaction"""

    def __init__(self, client=None):
        if client is None:
            from aira_live.db import db
            client = db()
        self._c = client

    async def run(self, fn):
        from google.cloud.firestore import async_transactional

        transaction = self._c.transaction()

        @async_transactional
        async def _inner(t):
            return await fn(_FsTxn(self._c, t))

        return await _inner(transaction)

    async def get(self, path: str) -> dict | None:
        snap = await self._c.document(path).get()
        return snap.to_dict() if snap.exists else None

    async def set(self, path: str, data: dict) -> None:
        await self._c.document(path).set(data)

    async def list(self, collection_path: str) -> list[tuple[str, dict]]:
        docs = [(d.id, d.to_dict()) async for d in self._c.collection(collection_path).stream()]
        return sorted(docs, key=lambda x: x[0])

    async def find_nearest(self, collection_path: str, field: str, query: list[float], *, limit: int = 3,
                           threshold: float | None = None) -> list[tuple[str, dict, float]]:
        """Firestore vector search (GA): ``collection.find_nearest`` with the COSINE measure. Needs the vector
        index from shared/infra/firestore.indexes.json; without it Firestore raises and the caller falls back."""
        from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
        from google.cloud.firestore_v1.vector import Vector

        q = self._c.collection(collection_path).find_nearest(
            vector_field=field, query_vector=Vector(query), limit=limit, distance_measure=DistanceMeasure.COSINE,
            distance_result_field="_distance", distance_threshold=threshold)
        out = []
        async for snap in q.stream():
            d = snap.to_dict() or {}
            out.append((snap.id, d, float(d.pop("_distance", 0.0))))
        return out

    def server_ts(self) -> object:
        from google.cloud.firestore import SERVER_TIMESTAMP
        return SERVER_TIMESTAMP


_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = FirestoreStore()
    return _store


def set_store(store: Store | None) -> None:
    global _store
    _store = store
