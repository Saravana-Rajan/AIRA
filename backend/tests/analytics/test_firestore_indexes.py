"""shared/infra/firestore.indexes.json: the composite indexes for database "aira", checked against the analytics queries.

How Firestore picks an index (https://firebase.google.com/docs/firestore/query-data/index-overview):
- A query that filters or orders on one field only is served by the automatic single-field indexes.
- Equality filters alone (on any number of fields) are served by merging single-field indexes.
- Equality filters plus a range filter (or order_by) on another field need a composite index: the equality
  fields first, then the range field in the query's sort direction. With no order_by, an inequality sorts
  its field ASCENDING, and a DESCENDING index does not serve it (one index per direction).

firebase.json deploys ONE indexes file for database "aira": shared/infra/firestore.indexes.json. There is no separate
analytics file. The analytics code issues single-field queries only (SINGLE_FIELD), so it needs no composite
index. The three ASCENDING time-window indexes (WINDOW) already exist in the deployed database; they are kept
in the one file so it matches what is deployed, and so a filter later pushed down to the server is served.
When an analytics query changes shape, update the lists here: the test then fails until an index serves it.
"""

import json
from pathlib import Path
from typing import NamedTuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
INDEXES = REPO_ROOT / "shared" / "infra" / "firestore.indexes.json"
STRAY = REPO_ROOT / "shared" / "infra" / "firestore.indexes.analytics.json"

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

# The same reads with the filter pushed to the server. Not issued today; their ASCENDING indexes are deployed.
WINDOW = {
    "forecast.daily_demand(shop, sku): ledger sku == X, reason == SALE, at >= since":
        q("ledger", ("sku", "reason"), ("at", ASC)),
    "forecast window, sales only: ledger reason == SALE, at >= since":
        q("ledger", ("reason",), ("at", ASC)),
    "summary.today, completed only: sales state == SALE_COMPLETED, completedAt >= start":
        q("sales", ("state",), ("completedAt", ASC)),
}

QUERIES = {**SINGLE_FIELD, **WINDOW}


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
def indexes() -> list[dict]:
    return _load(INDEXES)["indexes"]


# ---------- the index model follows Firestore's rules ----------

def test_single_field_and_equality_only_queries_need_no_composite():
    for name, query in SINGLE_FIELD.items():
        assert not needs_composite(query), name
    assert not needs_composite(q("ledger", ("sku", "reason")))


def test_equality_plus_range_needs_composite():
    for name, query in WINDOW.items():
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


# ---------- shared/infra/firestore.indexes.json ----------

def test_one_indexes_file_and_no_stray_analytics_file():
    assert INDEXES.exists()
    assert not STRAY.exists(), "merge it into shared/infra/firestore.indexes.json: firebase.json deploys only that file"


def test_file_uses_firebase_cli_format():
    data = _load(INDEXES)
    assert set(data) == {"indexes", "fieldOverrides"}
    assert isinstance(data["indexes"], list) and data["indexes"]
    assert data["fieldOverrides"] == []


def test_every_index_is_well_formed(indexes):
    for index in indexes:
        assert set(index) == {"collectionGroup", "queryScope", "fields"}, index
        assert index["queryScope"] == "COLLECTION", index
        assert isinstance(index["collectionGroup"], str) and index["collectionGroup"], index
        assert len(index["fields"]) >= 2, f"single-field indexes are automatic: {index}"
        for field in index["fields"]:
            assert set(field) == {"fieldPath", "order"}, field
            assert isinstance(field["fieldPath"], str) and field["fieldPath"], field
            assert field["order"] in {ASC, DESC}, field


def test_no_duplicate_indexes(indexes):
    shapes = [_shape(i) for i in indexes]
    assert len(shapes) == len(set(shapes))


def test_every_analytics_query_is_served(indexes):
    missing = [
        name for name, query in QUERIES.items()
        if needs_composite(query) and not any(serves(index, query) for index in indexes)
    ]
    assert not missing, f"no index serves: {missing}"


def test_the_deployed_window_indexes_are_kept(indexes):
    """The ASCENDING window indexes exist in the deployed database: dropping them from the file would make a
    firebase deploy offer to delete them."""
    deployed = {
        ("ledger", (("sku", ASC), ("reason", ASC), ("at", ASC))),
        ("ledger", (("reason", ASC), ("at", ASC))),
        ("sales", (("state", ASC), ("completedAt", ASC))),
    }
    assert deployed <= {_shape(i) for i in indexes}
