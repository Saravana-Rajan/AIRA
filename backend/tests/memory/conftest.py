"""In-memory Firestore + session fakes for memory/product tests (no emulator needed).

This ``fake_db`` overrides the shared one in ``tests/conftest.py`` for the memory tests only: it keys the
store by path tuples (``("shops", "demo", "locations", sku)``) and supports ``order_by``/``limit`` queries.
"""
import itertools

import pytest

from aira_live.frames import FrameStore


class FakeSnapshot:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return None if self._data is None else dict(self._data)


class FakeQuery:
    def __init__(self, coll, order=None, desc=False, lim=None):
        self._coll, self._order, self._desc, self._lim = coll, order, desc, lim

    def order_by(self, field, direction="ASCENDING"):
        return FakeQuery(self._coll, field, direction == "DESCENDING", self._lim)

    def limit(self, n):
        return FakeQuery(self._coll, self._order, self._desc, n)

    async def stream(self):
        docs = self._coll._docs()
        if self._order:
            docs.sort(key=lambda s: s.to_dict()[self._order], reverse=self._desc)
        if self._lim is not None:
            docs = docs[: self._lim]
        for d in docs:
            yield d


class FakeVectorQuery:
    """``collection.find_nearest(...)`` computed locally with the cosine measure; ``raises`` fakes a missing index."""

    def __init__(self, coll, field, vector, limit, threshold, raises):
        self._coll, self._field, self._vector = coll, field, vector
        self._limit, self._threshold, self._raises = limit, threshold, raises

    async def stream(self):
        from aira_live.embeddings import nearest_local, values_of

        if self._raises:
            raise self._raises
        rows = [(s.id, s.to_dict()) for s in self._coll._docs()]
        for doc_id, doc, _ in nearest_local(rows, values_of(self._vector), field=self._field, limit=self._limit,
                                            threshold=self._threshold):
            yield FakeSnapshot(doc_id, doc)


class FakeCollection(FakeQuery):
    _ids = itertools.count(1)
    find_nearest_raises: Exception | None = None     # set by a test to fake "no vector index yet"

    def __init__(self, store, path):
        self._store, self._path = store, path
        super().__init__(self)

    def document(self, doc_id=None):
        return FakeDoc(self._store, self._path + (doc_id or f"auto{next(FakeCollection._ids)}",))

    def find_nearest(self, vector_field, query_vector, limit, distance_measure, *, distance_result_field=None,
                     distance_threshold=None):
        return FakeVectorQuery(self, vector_field, query_vector, limit, distance_threshold,
                               FakeCollection.find_nearest_raises)

    def _docs(self):
        n = len(self._path) + 1
        return [FakeSnapshot(p[-1], v) for p, v in self._store.items() if len(p) == n and p[:-1] == self._path]


class FakeDoc:
    def __init__(self, store, path):
        self._store, self._path = store, path
        self.id = path[-1]

    def collection(self, name):
        return FakeCollection(self._store, self._path + (name,))

    async def get(self):
        return FakeSnapshot(self.id, self._store.get(self._path))

    async def set(self, data, merge=False):
        if merge and self._path in self._store:
            self._store[self._path] = {**self._store[self._path], **data}
        else:
            self._store[self._path] = dict(data)

    async def delete(self):
        self._store.pop(self._path, None)


class FakeDB:
    def __init__(self):
        self.store = {}

    def collection(self, name):
        return FakeCollection(self.store, (name,))


class FakeSession:
    """Same surface as ``aira_live.session.Session`` that the memory tools use; records what it sends."""

    def __init__(self, shop_id="demo", lang="en"):
        self.session_id, self.uid, self.shop_id = "s1", "u1", shop_id
        self.lang, self.verbosity = lang, "short"
        self.frames = FrameStore()
        self.sent, self.requested = [], []

    async def send(self, msg):
        self.sent.append(msg)

    async def request_frames(self, purpose, count=1, interval_ms=0, timeout_s=4.0):
        self.requested.append(purpose)
        return self.frames.latest(count, purpose=purpose)

    async def look_frame(self, purpose, timeout_s=4.0, **_kw):
        return await self.request_frames(purpose, count=1, timeout_s=timeout_s)


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr("aira_live.db.db", lambda: db)
    return db


@pytest.fixture
def session():
    return FakeSession()
