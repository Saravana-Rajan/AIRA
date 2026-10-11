# AIRA deploy freeze and submission runbook (Sat 18 Oct 2026, IST)

Owner: Saravana, the team leader. A second teammate reads each step aloud and ticks it.

This is a checklist for people. Nothing in this repo deploys, tags, pushes or submits anything: the owner types every command below by hand, on the day.

| When (IST) | What |
|---|---|
| 12:00 | Final build: everything merged, tests green, files ready |
| 18:00 | Deploy freeze |
| 18:15 | Generate the links, run every check |
| 19:15 | Commit the links, tag the submission |
| 19:30 | Fill the Hack2skill form |
| **20:00 IST** | Our target: the form is submitted |
| 18 October 2026, 11:59 PM IST | The form closes. The gap after 20:00 is a safety margin, not a plan |
| Oct 19 – Nov 6 | Evaluation window: everything stays frozen |

## Before you start

The setup this runbook assumes (build overrides, sections 1 and 3a):

- Backend: Cloud Run service `aira-live` in `asia-south1`. Min 1 and max 1 instance, timeout 3600 s, session affinity on.
- Frontend: one Firebase Hosting site, `aira-shop`. The phone app is at `/` and the Shop Dashboard at `/dashboard/`.
- Data: Firestore database `aira`, demo shop `murugan_dairy`.
- Deploys: only `.github/workflows/deploy.yml`, on every push to `main` of Saravana-Rajan/AIRA.

On the laptop you need `git`, `gh` (signed in, with write access to the repo), `gcloud` (signed in as the project owner), `python3`, `uv`, `curl`, `ffprobe` (from ffmpeg) and `pdftotext` (from poppler).

The project ID never goes into the repo, the Log, the team chat or a screenshot. Run everything from the root of a clone of the repo, on `main`, after setting the ID in your shell from your local, git-ignored `.env`:

```bash
export GCP_PROJECT='<copy from your local .env>'
test -n "$GCP_PROJECT" && echo "project set"
```

## 12:00 IST — final build

- [ ] Every PR the demo needs is merged to `main`. Nothing else merges today.
- [ ] The latest Deploy run on `main` is green, and none is still running:
  ```bash
  gh run list --repo Saravana-Rajan/AIRA --workflow deploy.yml --branch main --limit 3
  ```
- [ ] Every test suite passes on a clean, up-to-date `main`:
  ```bash
  git fetch origin && git switch main && git pull --ff-only
  npm ci && npm test
  (cd services/live && uv sync --frozen && uv run pytest -q)
  uv run --no-project --python 3.12 --with pytest python -m pytest scripts/tests -q
  ```
- [ ] No keys in the repo. This prints nothing:
  ```bash
  git grep -nE 'AIza[0-9A-Za-z_-]{20,}|BEGIN [A-Z ]*PRIVATE KEY-----' -- . ':!*.example'
  ```
- [ ] The project ID is not in the repo. This prints nothing:
  ```bash
  git grep -n "$GCP_PROJECT"
  ```
- [ ] The demo shop looks right. Open `https://aira-shop.web.app/dashboard/` and check the Murugan Dairy stock (Aavin milk, Sakthi curd, Arun ice cream). Only if rehearsals left it in a bad state, re-seed it. Read the docstring of `scripts/seed_firestore.py` first: it rewrites the demo shop's product, stock and supplier documents in database `aira`, never `(default)`.
  ```bash
  uv run --project services/live python scripts/seed_firestore.py
  ```
- [ ] The video decision is made, following the decision rule in `docs/submission/video-checklist.md`:
  - support's written approval for up to 4:00 is on file: submit the 3:50 cut, `docs/submission/aira-demo.mp4`
  - otherwise (the default): submit the 2:55 cut, `docs/submission/aira-demo-short.mp4`
- [ ] The final files are on this laptop:
  - [ ] `docs/submission/description.txt` (committed)
  - [ ] `docs/submission/AIRA-deck.pdf`, exported from the official 16-slide template (git-ignored)
  - [ ] the chosen video cut (git-ignored), already uploaded to YouTube as Public, following `docs/submission/video-checklist.md`
- [ ] The deck PDF passes its text guard (16 pages, every rubric phrase, no project ID):
  ```bash
  python3 scripts/check_deck_pdf.py docs/submission/AIRA-deck.pdf
  ```

## 18:00 IST — deploy freeze

Freeze the pipeline first, so that no later push to `main` (including the links commit at 19:15) can deploy.

- [ ] Disable the Deploy workflow, then confirm that its state is `disabled_manually`:
  ```bash
  gh workflow disable deploy.yml --repo Saravana-Rajan/AIRA
  gh workflow list --all --repo Saravana-Rajan/AIRA
  ```
- [ ] No Deploy run is still in progress. Disabling a workflow does not stop a run that has already started, so wait for any running one to finish:
  ```bash
  gh run list --repo Saravana-Rajan/AIRA --workflow deploy.yml --limit 3
  ```
- [ ] Record the serving revision and the commit it was built from. `deploy.yml` tags each image with its commit SHA:
  ```bash
  REV=$(gcloud run services describe aira-live --region asia-south1 --project "$GCP_PROJECT" \
    --format='value(status.latestReadyRevisionName)')
  IMAGE=$(gcloud run revisions describe "$REV" --region asia-south1 --project "$GCP_PROJECT" \
    --format='value(spec.containers[0].image)')
  DEPLOYED_SHA=${IMAGE##*:}
  echo "$REV $DEPLOYED_SHA"
  git fetch origin && git rev-parse origin/main   # must print the same SHA as DEPLOYED_SHA
  ```
  Write `REV` and `DEPLOYED_SHA` in the Log. Never paste `IMAGE`: its path contains the project ID.
- [ ] If the two SHAs differ, stop: `main` has a commit that never deployed. Re-enable the workflow, re-run its last run from the Actions tab, wait for it to go green, and start this section again.
- [ ] Pin all traffic to that revision. A revision created later then gets no traffic on its own:
  ```bash
  gcloud run services update-traffic aira-live --region asia-south1 --project "$GCP_PROJECT" \
    --to-revisions="$REV=100"
  ```
- [ ] Check the traffic and the service shape:
  ```bash
  gcloud run services describe aira-live --region asia-south1 --project "$GCP_PROJECT" \
    --format='yaml(status.traffic)'
  gcloud run services describe aira-live --region asia-south1 --project "$GCP_PROJECT" \
    --format='yaml(spec.template.metadata.annotations,spec.template.spec.timeoutSeconds)'
  ```
  Expect:
  - one traffic entry: `percent: 100` on `revisionName` `$REV`
  - `autoscaling.knative.dev/minScale: '1'` and `autoscaling.knative.dev/maxScale: '1'`
  - `run.googleapis.com/sessionAffinity: 'true'`
  - `timeoutSeconds: 3600`
- [ ] Write the time of the current Hosting release in the Log: Firebase console → Hosting → `aira-shop` → Release history, top entry. That is the release to roll back to.
- [ ] Tell the team in the chat that the freeze is on. From now until after Nov 6, nobody runs `gcloud run deploy` or `npx firebase-tools deploy`, changes Firestore rules or indexes, or adds new versions of the `aira-*` secrets in Secret Manager.

## 18:15 IST — generate the links and run every check

- [ ] Set the four link variables. `VIDEO_URL` is the public YouTube link from the upload step in `docs/submission/video-checklist.md`:
  ```bash
  export APP_URL=https://aira-shop.web.app
  export DASHBOARD_URL=https://aira-shop.web.app/dashboard/
  export LIVE_URL=$(gcloud run services describe aira-live --region asia-south1 --project "$GCP_PROJECT" \
    --format='value(status.url)')
  export VIDEO_URL='<public YouTube URL>'
  ```
- [ ] Render `docs/submission/links.json` from the template. If a variable is not set, its `${NAME}` stays in the file and the checker below fails on it:
  ```bash
  python3 -c 'import os, sys; sys.stdout.write(os.path.expandvars(sys.stdin.read()))' \
    < docs/submission/links.template.json > docs/submission/links.json
  cat docs/submission/links.json
  ```
- [ ] Run the full checker on the chosen cut. It opens every link, so run it online. Expect `OK    all checks passed` and no `FAIL` line.
  - Default, with no written approval (the 2:55 cut). Expect no `WARN` line either:
    ```bash
    python3 scripts/check_submission.py --description docs/submission/description.txt \
      --links docs/submission/links.json --pdf docs/submission/AIRA-deck.pdf \
      --video docs/submission/aira-demo-short.mp4 --max-video 175
    ```
  - Only with support's written approval for up to 4:00 (the 3:50 cut). One `WARN` line about the length is expected:
    ```bash
    python3 scripts/check_submission.py --description docs/submission/description.txt \
      --links docs/submission/links.json --pdf docs/submission/AIRA-deck.pdf \
      --video docs/submission/aira-demo.mp4 --max-video 240
    ```
- [ ] The backend is up and running for real. Expect `{"ok":true,"mock":false}`. `"mock":true` means the Gemini key is not mounted:
  ```bash
  curl -fsS "$LIVE_URL/health"
  ```
- [ ] Open every link in `docs/submission/links.json` in a private (logged-out) browser window. The video plays, the repo is public, and the app and the dashboard load with no sign-in.
- [ ] Live app on a fresh Android phone: allow the mic and the camera, run one sale end to end, and watch the dashboard update.
- [ ] Judge demo mode on a laptop: `https://aira-shop.web.app/?demo=1` opens the demo page, every scenario card runs, and its dashboard link works.

## 19:15 IST — commit the links and tag the submission

`deploy.yml` is disabled, so this push cannot deploy. It is the last push to `main` until after Nov 6.

- [ ] Repo hygiene, right before the tag. If any line prints something other than the comment says, stop and fix it with the team first:
  ```bash
  test -n "$GCP_PROJECT" && echo "project set"
  git grep -n "$GCP_PROJECT"                        # prints nothing
  git log --all -p | grep -c "$GCP_PROJECT"         # prints 0
  git log --format='%(trailers:only)' | grep -c .   # prints 0: no co-author or session trailers
  ```
- [ ] Commit the links, check that only the links changed since the deployed commit, then tag and push:
  ```bash
  git status --short                                # only docs/submission/links.json
  git add docs/submission/links.json
  git commit -m "docs(submission): submitted links"
  git diff --stat "$DEPLOYED_SHA" HEAD              # only docs/submission/links.json
  git tag v1.0.0-submission
  git push origin main v1.0.0-submission
  ```
  If `git diff --stat` lists any other file, do not tag. Go back to the SHA check at 18:00.
- [ ] Confirm the tag is pushed and that no Deploy run started:
  ```bash
  git ls-remote --tags origin v1.0.0-submission
  gh run list --repo Saravana-Rajan/AIRA --limit 3
  ```

## 19:30 IST — fill the Hack2skill form

Form: https://hack2skill.com/event/aibuildercup2026/dashboard/submissions, signed in as the team leader. Copy every value from the files; type nothing by hand.

| Field on the form | Value |
|---|---|
| Challenge | **Sustainability & Social Impact** |
| Prototype link deployed on GCP or Cloud Run | `app` from `docs/submission/links.json` (https://aira-shop.web.app) |
| Deck: final prototype PPT/deck converted into PDF | upload `docs/submission/AIRA-deck.pdf` (16 slides, under 5 MB) |
| GitHub repository link (public access) | https://github.com/Saravana-Rajan/AIRA |
| Demo video link | `video` from `docs/submission/links.json` |
| Brief description | paste `docs/submission/description.txt` exactly, with no edits in the browser |

- [ ] Copy the description to the clipboard and paste it into the form:
  ```bash
  tr -d '\n' < docs/submission/description.txt | pbcopy
  ```
- [ ] The form's character counter shows 1024 or fewer.
- [ ] Click **Submit** before **20:00 IST**.
- [ ] Take a screenshot of the confirmation page and save the confirmation email as a PDF. Keep both in the team drive, not in the repo.
- [ ] If the 3:50 cut was submitted, save support's written approval as a PDF next to them.
- [ ] The dashboard shows the submission as submitted. Write the time in the Log.

## Oct 19 – Nov 6 — evaluation window

Judges evaluate the deployment as it works when they open it. An outage gets no retry.

- [ ] No pushes to main: work can go on in branches and PRs, but nothing merges. No `firebase deploy` (or `npx firebase-tools deploy`), no traffic changes on `aira-live`, and no Firestore rules or index changes.
- [ ] Never touch the Firestore `(default)` database. AIRA uses `aira`. Leave the `murugan_dairy` demo shop as submitted.
- [ ] `deploy.yml` stays disabled until after Nov 6. Check it every Monday:
  ```bash
  gh workflow list --all --repo Saravana-Rajan/AIRA
  ```
- [ ] Every day at 10:00 IST, check the backend (`LIVE_URL` is the `api_health` host in `docs/submission/links.json`). Then open the app and `/dashboard/` in a private window, and post the result in the team chat:
  ```bash
  curl -fsS "$LIVE_URL/health"
  ```
- [ ] If the uptime check from `docs/ops/monitoring.md` is set up, its alerts go to Saravana's phone.
- [ ] Every Monday, check the credits balance on the Cloud console billing page. There are no budget alerts (shared billing account), so this check is the only warning.
- [ ] Keep the repo public. Never force-push `main` and never move the `v1.0.0-submission` tag.

## If something breaks (any time after 18:00)

- [ ] Look first: Cloud console → Cloud Run → `aira-live` → Logs, and the service status. Most failures are a missing secret or a quota, not the code.
  ```bash
  gcloud run services describe aira-live --region asia-south1 --project "$GCP_PROJECT" \
    --format='yaml(status.conditions,status.traffic)'
  ```
- [ ] If traffic moved off the frozen revision, put it back. `REV` is in the Log:
  ```bash
  REV='<revision from the Log>'
  gcloud run services update-traffic aira-live --region asia-south1 --project "$GCP_PROJECT" \
    --to-revisions="$REV=100"
  ```
- [ ] If Hosting changed, roll it back: Firebase console → Hosting → `aira-shop` → Release history → the release from the Log → Rollback.
- [ ] Redeploy only if the frozen revision itself cannot serve, and only the tagged code. Re-enable the workflow, re-run the last Deploy run from the Actions tab, then disable the workflow again. Pin the new revision as at 18:00 (new `REV`, same `update-traffic` command) and write what changed, and why, in the Log and the team chat.

## Log

Keep this table in the team chat or on paper, not in the repo: nothing is pushed to `main` after 19:15. Never write the project ID, an image path, an email address or a phone number in it.

| What | Value |
|---|---|
| Video cut chosen, and why | |
| Deploy workflow disabled at | |
| `REV` (serving revision) | |
| `DEPLOYED_SHA` | |
| Hosting release time (Release history) | |
| Checker result | |
| `/health` result | |
| Tag pushed at | |
| Form submitted at | |
| Screenshot and email PDF saved | |
