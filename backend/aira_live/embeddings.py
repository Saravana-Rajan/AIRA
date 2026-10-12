"""Text embeddings for Firestore vector search (products and last-seen sightings).

One embedder, two uses: ``business.catalog`` matches "the blue packet curd" to a product when the exact /
alias match fails, and ``memory.items`` matches "keys" to a sighting labelled "key bunch". The vector lives in
the field ``embedding`` (``google.cloud.firestore_v1.vector.Vector``) of shops/{shop}/products/{sku} and
shops/{shop}/memory/{id}; the index definitions are in shared/infra/firestore.indexes.json.

Every call is best effort: ``embed`` returns None (never raises) when there is no key, the model fails or the
response is malformed, and the callers then behave exactly as they did without vector search.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Protocol

log = logging.getLogger(__name__)

# gemini-embedding-2 accepts 128-3072 output dimensions (768 / 1536 / 3072 recommended); a Firestore vector
# index allows at most 2048. 768 keeps the index small and the match quality good enough for a shop catalog.
DIM = 768
FIELD = "embedding"
# gemini-embedding-2 takes no task_type: the task goes in the text itself (docs: "task: search result | query: ...").
DOC_PREFIX = "task: search result | query: "
QUERY_PREFIX = "task: search result | query: "
MAX_CHARS = 2000


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class GeminiEmbedder:
    """``models.embed_content`` with the ``embed`` role of aira_live.models, through the shared genai client."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        from google.genai import types

        from aira_live.models import model_for
        from aira_live.vision.gemini import client

        res = await client().aio.models.embed_content(
            model=model_for("embed"), contents=texts,
            config=types.EmbedContentConfig(output_dimensionality=DIM))
        out = [list(e.values) for e in (res.embeddings or [])]
        if len(out) != len(texts) or any(len(v) != DIM for v in out):
            raise ValueError(f"embedding response has {len(out)} vectors of {[len(v) for v in out][:3]} dims")
        return out


class FakeEmbedder:
    """Tests: a deterministic bag-of-words vector, so similar texts are close and unrelated ones are far.

    ``synonyms`` fold words together ("bunch" -> "keys"); ``focus`` words weigh 1.0 and every other word 0.1,
    which gives the sparse fake the geometry of a real model (the meaningful words dominate the distance)."""

    def __init__(self, synonyms: dict[str, str] | None = None, focus: set[str] | frozenset[str] | None = None):
        self.synonyms = synonyms or {}
        self.focus = set(focus) if focus is not None else None
        self.calls: list[list[str]] = []

    def _vec(self, text: str) -> list[float]:
        import re
        import zlib

        v = [0.0] * DIM
        body = text.split("| query: ", 1)[-1]
        for tok in re.findall(r"\w+", body.casefold()):
            tok = self.synonyms.get(tok, tok)
            weight = 1.0 if self.focus is None or tok in self.focus else 0.1
            v[zlib.crc32(tok.encode("utf-8")) % DIM] += weight
        return v

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vec(t) for t in texts]


class FailingEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedding unavailable")


_embedder: Embedder | None = None


def set_embedder(e: Embedder | None) -> None:
    """Inject an embedder (tests); None restores the Gemini one."""
    global _embedder
    _embedder = e


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = GeminiEmbedder()
    return _embedder


def _configured() -> bool:
    from aira_live import config

    try:
        return bool(config.load().gemini_api_key)
    except Exception:  # noqa: BLE001 - config errors mean "no embeddings", never a crash
        return False


async def embed(text: str, *, query: bool = False) -> list[float] | None:
    """The vector for one text (a document, or a spoken query with ``query=True``); None when it cannot be made."""
    text = " ".join((text or "").split())[:MAX_CHARS]
    if not text:
        return None
    emb = get_embedder()
    if isinstance(emb, GeminiEmbedder) and not _configured():
        return None
    try:
        vectors = await emb.embed([(QUERY_PREFIX if query else DOC_PREFIX) + text])
    except Exception as exc:  # noqa: BLE001 - any failure means "no vector", the callers fall back
        log.warning("embedding failed for %r: %s", text[:60], exc)
        return None
    return list(vectors[0]) if vectors and vectors[0] else None


def to_vector(values: list[float]) -> Any:
    """The Firestore ``Vector`` type for a list of floats (what the ``embedding`` field stores)."""
    from google.cloud.firestore_v1.vector import Vector

    return Vector([float(v) for v in values])


def values_of(vector: Any) -> list[float] | None:
    """Floats of a stored vector (``Vector``, list or tuple); None for anything else."""
    if vector is None:
        return None
    inner = getattr(vector, "value", None)
    if inner is None and hasattr(vector, "to_map_value"):
        inner = vector.to_map_value().get("value")
    if inner is None and isinstance(vector, (list, tuple)):
        inner = vector
    try:
        out = [float(v) for v in inner]  # type: ignore[union-attr]
    except (TypeError, ValueError):
        return None
    return out or None


def cosine_distance(a: list[float], b: list[float]) -> float:
    """1 - cosine similarity, the COSINE measure of Firestore ``find_nearest`` (0 = same direction, 2 = opposite)."""
    if len(a) != len(b) or not a:
        return 2.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 2.0
    return 1.0 - dot / (na * nb)


def product_text(doc: dict) -> str:
    """The words a product is embedded from: brand, name, pack, unit, look (colour / packaging), aliases (ta/hi/en)."""
    parts = [str(doc.get(k) or "") for k in ("brand", "name", "pack", "unit", "look")]
    for lang in ("en", "ta", "hi"):
        parts.extend(str(a) for a in (doc.get("aliases") or {}).get(lang, []) if a)
    seen: list[str] = []
    for p in parts:
        p = " ".join(p.split())
        if p and p.casefold() not in {s.casefold() for s in seen}:
            seen.append(p)
    return ". ".join(seen)


async def product_vector(doc: dict) -> Any | None:
    """``Vector`` for a product doc, or None (then the doc is written without an ``embedding`` field)."""
    values = await embed(product_text(doc))
    return to_vector(values) if values else None


def nearest_local(rows: list[tuple[str, dict]], query: list[float], *, field: str = FIELD, limit: int = 3,
                  threshold: float | None = None) -> list[tuple[str, dict, float]]:
    """``find_nearest`` over in-memory docs (MemoryStore and the test fakes): (id, doc, cosine distance), nearest first."""
    out: list[tuple[str, dict, float]] = []
    for doc_id, doc in rows:
        vec = values_of(doc.get(field))
        if vec is None:
            continue
        d = cosine_distance(query, vec)
        if threshold is None or d <= threshold:
            out.append((doc_id, doc, d))
    out.sort(key=lambda r: (r[2], r[0]))
    return out[:limit]


EmbedFn = Callable[[str], Awaitable[list[float] | None]]
