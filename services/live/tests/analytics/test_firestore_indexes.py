"""infra/firestore.indexes.analytics.json: the composite indexes the analytics queries need in database "aira".

How Firestore picks an index (https://firebase.google.com/docs/firestore/query-data/index-overview):
- A query that filters or orders on one field only is served by the automatic single-field indexes.
- Equality filters alone (on any number of fields) are served by merging single-field indexes.
- Equality filters plus a range filter (or order_by) on another field need a composite index: the equality
  fields first, then the range field in the query's sort direction. With no order_by, an inequality sorts
  its field ASCENDING, and a DESCENDING index does not serve it (one index per direction).

The foundation's infra/firestore.indexes.json holds the DESCENDING "latest first" listing indexes. The
analytics time-window queries ("since the start of the day / the window") are range filters with no
order_by, so they need the ASCENDING variants kept in the analytics file. firebase.json reads one indexes
file per database, so integration appends this file's "indexes" array to infra/firestore.indexes.json.

QUERIES is the list of analytics query shapes. When an analytics query changes shape, update it here:
the test then fails until some index serves the new shape.
"""

import json
from pathlib import Path
from typing import NamedTuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
ANALYTICS = REPO_ROOT / "infra" / "firestore.indexes.analytics.json"
FOUNDATION = REPO_ROOT / "infra" / "firestore.indexes.json"

ASC, DESC = "ASCENDING", "DESCENDING"


class Query(NamedTuple):
    collection: str
    equals: frozenset[str]
    order: tuple[str, str] | None  # (range or order_by field, direction); None = equality filters only


def q(collection: str, equals: tuple[str, ...] = (), order: tuple[str, str] | None = None) -> Query:
    return Query(collection, frozenset(equals), order)


# Shapes the analytics code issues today (forecast, summary, insights, jobs): single-field, no composite.
SINGLE_FIELD = {
    "forecast window: ledger at >= window start": q("ledger", order=("at", ASC)),
    "summary.today: sales completedAt >= start of IST day": q("sales", order=("completedAt", ASC)),
    "summary.today: orders state == DISCREPANCY": q("orders", ("state",)),
}

# The same reads with the filter pushed to the server; these need the analytics composite indexes.
COMPOSITE = {
    "forecast.daily_demand(shop, sku): ledger sku == X, reason == SALE, at >= since":
        q("ledger", ("sku", "reason"), ("at", ASC)),
    "forecast window, sales only: ledger reason == SALE, at >= since":
        q("ledger", ("reason",), ("at", ASC)),
    "summary.today, completed only: sales state == SALE_COMPLETED, completedAt >= start":
        q("sales", ("state",), ("completedAt", ASC)),
}

QUERIES = {**SINGLE_FIELD, **COMPOSITE}


# ---------- index model ----------

def _shape(index: dict) -> tuple:
    return index["collectionGroup"], tuple((f["fieldPath"], f["order"]) for f in index["fields"])


def needs_composite(query: Query) -> bool:
    return bool(query.equals) and query.order is not None and query.order[0] not in query.equals


def serves(index: dict, query: Query) -> bool:
    """True when this composite index serves the query: its fields are exactly the equality fields
    (any order and direction), then the range/order field in the query's direction."""
    if index["collectionGroup"] != query.collection or query.order is None:
        return False
    fields = [(f["fieldPath"], f["order"]) for f in index["fields"]]
    if len(fields) != len(query.equals) + 1:
        return False
    *head, last = fields
    return {name for name, _ in head} == query.equals and last == query.order


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _index(collection: str, *fields: tuple[str, str]) -> dict:
    return {
        "collectionGroup": collection,
        "queryScope": "COLLECTION",
        "fields": [{"fieldPath": name, "order": order} for name, order in fields],
    }


@pytest.fixture(scope="module")
def analytics() -> dict:
    if not ANALYTICS.exists():
        pytest.skip("infra/firestore.indexes.analytics.json is gone (merged into infra/firestore.indexes.json)")
    return _load(ANALYTICS)


@pytest.fixture(scope="module")
def foundation() -> list[dict]:
    return _load(FOUNDATION)["indexes"] if FOUNDATION.exists() else []


@pytest.fixture(scope="module")
def deployed(foundation) -> list[dict]:
    """Every index the repo defines for database "aira": the foundation file plus the analytics file."""
    return foundation + (_load(ANALYTICS)["indexes"] if ANALYTICS.exists() else [])


# ---------- the index model follows Firestore's rules ----------

def test_single_field_and_equality_only_queries_need_no_composite():
    for name, query in SINGLE_FIELD.items():
        assert not needs_composite(query), name
    assert not needs_composite(q("ledger", ("sku", "reason")))


def test_equality_plus_range_needs_composite():
    for name, query in COMPOSITE.items():
        assert needs_composite(query), name


def test_descending_index_does_not_serve_an_ascending_range():
    query = q("ledger", ("sku", "reason"), ("at", ASC))
    assert not serves(_index("ledger", ("sku", ASC), ("reason", ASC), ("at", DESC)), query)
    assert serves(_index("ledger", ("sku", ASC), ("reason", ASC), ("at", ASC)), query)


def test_equality_fields_match_in_any_order_and_direction():
    query = q("ledger", ("sku", "reason"), ("at", ASC))
    assert serves(_index("ledger", ("reason", DESC), ("sku", ASC), ("at", ASC)), query)


def test_index_must_match_collection_and_fields_exactly():
    query = q("ledger", ("reason",), ("at", ASC))
    assert not serves(_index("sales", ("reason", ASC), ("at", ASC)), query)
    assert not serves(_index("ledger", ("sku", ASC), ("reason", ASC), ("at", ASC)), query)
    assert not serves(_index("ledger", ("reason", ASC), ("delta", ASC)), query)


# ---------- the analytics indexes file ----------

def test_file_uses_firebase_cli_format(analytics):
    assert set(analytics) == {"indexes", "fieldOverrides"}
    assert isinstance(analytics["indexes"], list) and analytics["indexes"]
    assert analytics["fieldOverrides"] == []


def test_every_index_is_well_formed(analytics):
    for index in analytics["indexes"]:
        assert set(index) == {"collectionGroup", "queryScope", "fields"}, index
        assert index["queryScope"] == "COLLECTION", index
        assert isinstance(index["collectionGroup"], str) and index["collectionGroup"], index
        assert len(index["fields"]) >= 2, f"single-field indexes are automatic: {index}"
        for field in index["fields"]:
            assert set(field) == {"fieldPath", "order"}, field
            assert isinstance(field["fieldPath"], str) and field["fieldPath"], field
            assert field["order"] in {ASC, DESC}, field


def test_no_duplicate_indexes(analytics):
    shapes = [_shape(i) for i in analytics["indexes"]]
    assert len(shapes) == len(set(shapes))


def test_every_analytics_query_is_served(deployed):
    missing = [
        name for name, query in QUERIES.items()
        if needs_composite(query) and not any(serves(index, query) for index in deployed)
    ]
    assert not missing, f"no index serves: {missing}"


def test_every_analytics_index_serves_an_analytics_query(analytics):
    unused = [
        _shape(index) for index in analytics["indexes"]
        if not any(serves(index, query) for query in QUERIES.values())
    ]
    assert not unused, f"indexes no analytics query uses: {unused}"


def test_adds_only_indexes_the_foundation_lacks(analytics, foundation):
    if not foundation:
        pytest.skip("infra/firestore.indexes.json is not in the tree")
    ours = {_shape(i) for i in analytics["indexes"]}
    theirs = {_shape(i) for i in foundation}
    if ours <= theirs:
        pytest.skip("analytics indexes already merged into infra/firestore.indexes.json")
    overlap = ours & theirs
    assert not overlap, f"already in infra/firestore.indexes.json: {sorted(overlap)}"


def test_foundation_alone_does_not_serve_the_composite_queries(foundation):
    """Guards the reason this file exists: the foundation's DESCENDING indexes miss these ASCENDING windows.
    Skipped once the analytics indexes are merged into the foundation file."""
    if not foundation:
        pytest.skip("infra/firestore.indexes.json is not in the tree")
    served = [name for name, query in COMPOSITE.items() if any(serves(i, query) for i in foundation)]
    if len(served) == len(COMPOSITE):
        pytest.skip("analytics indexes already merged into infra/firestore.indexes.json")
    assert not served, f"foundation already serves: {served}"
