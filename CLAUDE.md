# AIRA — instructions for Claude Code

**What we're building:** AIRA is a voice-first assistant that lets a **blind shopkeeper run a small shop alone**. The setup is a chest-mounted phone, one earphone, Gemini Live, Gemini Robotics-ER 2, Firebase/Firestore and Cloud Run. The demo shop is *Murugan Dairy*, selling Aavin milk, Sakthi curd and Arun ice cream.

**Read first:**

1. `docs/superpowers/specs/2026-10-10-aira-design.md`
2. `docs/superpowers/plans/2026-10-10-00-team-plan.md`
3. `docs/superpowers/plans/2026-10-10-00-interfaces.md`: exact names. Never rename anything listed there.
4. Your own plans in `docs/superpowers/plans/<name>/`, in number order.

## Ownership — stay inside your area; ask the owner before editing theirs

| Owner | Area |
|---|---|
| **Saravana** | `packages/contracts`, `infra/` |
| | `services/live/aira_live/`: `contracts.py`, `config.py`, `models.py`, `db.py`, `ids.py`, `i18n/`, `session.py`, `frames.py`, `main.py`, `live_bridge.py`, `confirm.py`, `sessions.py`, `devtools/`, `analytics/`, `tools/insights.py` |
| **Ishwarya** | `apps/app/`, the `bench/` runner |
| **Sarmitha** | `services/live/aira_live/vision/`, `memory/`, `tools/products.py` |
| **Kanish** | `services/live/aira_live/business/`, `messaging/`, `tools/{orders,delivery,payments,sales,stock}.py`, `apps/dashboard/`, `content/catalog/` |

## Rules

- **The model proposes, the backend commits.** Gemini never writes stock, money or state. Tools call deterministic services that validate and commit inside Firestore transactions, using `ids.idem_key(...)`.
- **Evidence before state:**
  - An order is not a delivery.
  - An invoice is not proof of receipt.
  - A saved location is not proof the product is still there.
  - A payment is confirmed only by a cash count, a soundbox parse, or explicit owner confirmation.
- **Confirm before consequence.** Ordering, paying and completing a sale always go through `confirm.propose(...)` and wait for a spoken "yes".
- **Uncertainty is allowed; invention is not.** If two reads disagree, say "not sure" and ask for another view. Never invent counts or amounts. Never claim to detect counterfeit notes.
- **Model IDs** live only in `services/live/aira_live/models.py`. Coordinates are `[y, x]` / `[ymin, xmin, ymax, xmax]`, normalised 0–1000.
- **Contracts are frozen.** Change them only through one PR that updates `packages/contracts/src/*.ts`, `aira_live/contracts.py` and `packages/contracts/fixtures/*.json` together. Saravana reviews.
- **No secrets in git.** Load `GEMINI_API_KEY`, `WHATSAPP_*` and `TELEGRAM_TOKEN` from env or Secret Manager.
- **TDD:** write the failing test first.
  - TypeScript: `npm test -w <workspace>`
  - Python: `cd services/live && uv run pytest -q`
- **Branches:** use `<name>/<topic>` and open small PRs to `main` at least twice a day. `main` must always be deployable.
- **User-facing strings** come in `ta`, `hi` and `en` via `aira_live.i18n.t`. Keep sentences short.
- **Product name:** AIRA. Payments are cash or UPI only.
