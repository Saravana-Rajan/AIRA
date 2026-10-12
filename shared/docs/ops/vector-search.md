# Firestore vector search (products and last-seen memory)

AIRA stores a Gemini embedding in the field `embedding` (Firestore `Vector`, 768 dimensions, model role
`embed` in `backend/aira_live/models.py` = `gemini-embedding-2`) on:

- `shops/{shop}/products/{sku}`: brand, name, pack, unit, `look` (colour / packaging words) and the ta/hi/en
  aliases, so "the blue packet curd" or "the round tub" finds the right product when the alias match fails
  (`aira_live.business.catalog.resolve`, then `did_you_mean` asks "Did you mean Sakthi Curd 500 ml?").
- `shops/{shop}/memory/{id}`: the sighting's item label, so `memory_where("keys")` matches "key bunch".

Both queries use `find_nearest` with the COSINE measure, limit 3 and a distance threshold. Without a key, an
index or on any error they fall back to the old behaviour.

## Indexes

Defined in `shared/infra/firestore.indexes.json` (deployed with `firebase deploy --only firestore:indexes`
from `shared/infra`). To create them with gcloud instead (database `aira`, dimension 768 = `aira_live.embeddings.DIM`):

```
gcloud firestore indexes composite create --collection-group=products --query-scope=COLLECTION --field-config=field-path=embedding,vector-config='{"dimension":"768","flat":"{}"}' --database=aira
gcloud firestore indexes composite create --collection-group=memory --query-scope=COLLECTION --field-config=field-path=embedding,vector-config='{"dimension":"768","flat":"{}"}' --database=aira
```

## Backfill

```
uv run --project backend python shared/scripts/embed_catalog.py                       # dry run: prints what would change
uv run --project backend python shared/scripts/embed_catalog.py --yes-write-cloud --project <gcp-project>
```

The seeder (`shared/scripts/seed_firestore.py`, `aira_live.business.seed`) writes the vectors when
`GEMINI_API_KEY` is set; `product_add` / `product_update` keep them current.
