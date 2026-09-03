# Deployment

## Short version

| Where | Works? | Why |
|---|---|---|
| **Your own machine + Tailscale** | ✅ best fit | Free, private, no public attack surface, disk is real |
| **Fly.io** | ✅ recommended cloud | Persistent volume, always-on process, `fly.toml` is already in the repo |
| **Railway / Render** | ✅ browser-only path | Deploy straight from GitHub, add a persistent disk, no CLI needed |
| **Vercel** | ❌ wrong shape | No persistent disk, no long-running process, no real scheduler |
| **Netlify / Cloudflare Pages** | ❌ same reasons | Same serverless model |
| **GitHub Pages** | ⚠️ snapshot only | Cannot host the app at all, but does host a read-only static export of it — see below |

## Why not Vercel

Not a knock on Vercel — this app is simply not the shape Vercel hosts.

1. **The filesystem is ephemeral.** Vercel's Python runtime is serverless
   functions; everything outside `/tmp` is read-only, and `/tmp` does not
   survive. Hofradar's entire value proposition is *a database that remembers
   what it has seen since February*. On Vercel it would forget on every cold
   start. You would have to move to an external Postgres (Neon, Supabase) —
   which is supported, but that is now two services for what a $5 machine does
   with one volume.

2. **A crawl is not a request.** Function invocations are duration-capped
   (seconds, tens of seconds — configurable, but not "however long fifteen
   sources take"). A pipeline run walks sitemaps, geocodes, routes and
   optionally calls an LLM. It is a background job measured in minutes, not an
   HTTP handler.

3. **There is no persistent scheduler.** Vercel Cron pings an HTTP endpoint,
   which lands you back in problem 2. The weekly run needs a process that stays
   alive, which is exactly what `docker-compose.yml`'s `scheduler` service is.

So: the UI *could* live on Vercel with an external Postgres, but the crawler and
the scheduler still need a real host. One host is simpler than three.

## Do you need to wait for a local machine?

**No.** Two of the three good options are fully browser-driven.

### Option A — Render or Railway, entirely from a browser

Nothing to install. Both read the `Dockerfile` in this repo.

1. Push this branch (already done) and connect the repo in the dashboard.
2. Create a **Web Service** from the repo, Docker runtime, port `8000`.
3. Attach a **persistent disk / volume** mounted at `/data`. **Do not skip
   this** — without it the database is wiped on every deploy.
4. Set environment variables:
   - `HOFRADAR_DATA_DIR=/data`
   - `HOFRADAR_PASSWORD_HASH=…` (see below)
   - `ANTHROPIC_API_KEY=…` (optional)
5. Deploy. The `/healthz` endpoint is already wired for their health checks.

Note that a persistent disk generally requires a paid instance tier on Render;
Railway bills by usage. Both are a few euros a month at this size.

### Option B — Fly.io, one CLI command from any machine

Cheapest and the nicest fit, and `fly.toml` is already written. You need
`flyctl` once, on any machine with a browser for `fly auth login`:

```bash
fly launch --no-deploy
fly volumes create hofradar_data --size 3
fly secrets set HOFRADAR_PASSWORD_HASH="$(hofradar hash-password)"
fly deploy
```

`auto_stop_machines = "suspend"` is set, so it sleeps when you are not looking
at it and wakes on request — the weekly crawl is the only thing that needs it
awake, and that is what the scheduler process is for.

### Option C — your own machine plus Tailscale (my actual recommendation)

For a personal research tool holding your own search history, the best hosting
is often no public hosting:

```bash
docker compose up -d
tailscale up            # then reach it at http://<machine>:8000 from anywhere
```

Zero cost, zero public attack surface, the disk is a real disk, and you can
still open it from your phone. The password gate still applies — belt and
braces.

## The public snapshot on GitHub Pages

`https://danm3rcurius.github.io/real-estate-tracker/` is **not** this
application. It is a directory of static files produced by
`hofradar export-site` and published by `.github/workflows/pages.yml`.

It exists to show what the tool does without asking anyone to install anything.
What it shows is **invented**: twelve fictional farmsteads in real Bavarian
towns, from `data/seed/demo_listings.yaml`. Every page carries a banner saying
so.

That is not squeamishness. Pages serves files, so there is no process to run the
password gate, so everything published there is world-readable — and this
repository is public. A snapshot of your actual radar would be your search
history on the open web, permanently, in git history and in search indexes. The
workflow enforces this rather than trusting it: the build fails if any property
in the database it just built is not an `example.invalid` listing.

### What works in the snapshot, and what does not

| | |
|---|---|
| The two sliders | **Live.** `app.js` recomputes the derived driving and purchase bands client-side. |
| The ranked list, map and dossiers | **Frozen** at build time. Moving a slider does not re-query — there is nothing to query. |
| Triage, report generation, "Jetzt suchen", the paste box, settings | **Absent.** Each is replaced by a line saying it needs a database. |
| CSV and JSON export | Present, as the files they were at build time. |

### Building it yourself

```bash
hofradar init-db
hofradar seed-demo                      # or point it at your own database
hofradar export-site --out site --base-path /real-estate-tracker
python -m http.server -d site 8080      # then open http://localhost:8080/real-estate-tracker/
```

`--base-path` is the subdirectory the site is served from. A project Pages site
lives under `/<repo>/`; for a user or organisation site served from the root,
leave it off.

Nothing stops you pointing the exporter at a real database — it is the same
command — but then **do not publish the output anywhere public.** It has no
password gate and cannot be given one.

### Turning it on

One setting, once, by hand: **Settings → Pages → Source: GitHub Actions**. The
workflow cannot set this itself, and until it is set the deploy step fails with
an error saying exactly that.

## The password gate

One password, one signed cookie. There is exactly one user, so there is no
account model.

```bash
hofradar hash-password          # prompts, prints a pbkdf2_sha256$… string
```

Put the result in `HOFRADAR_PASSWORD_HASH`. A plain `HOFRADAR_PASSWORD` also
works for a quick local test.

**With neither set, there is no gate** and the app logs a warning at startup.
That is deliberate — `hofradar serve` on localhost should not make you type a
password — but it means you must set one before the app has a public URL.

What the gate does:

- constant-time password comparison; PBKDF2-SHA256 at 240k rounds for the hash
- session cookie is `HttpOnly`, `SameSite=Lax`, and `Secure` whenever the
  request arrived over HTTPS (detected via `X-Forwarded-Proto`, which is what
  Fly, Render and Railway set)
- the cookie holds no data, only an expiry and an HMAC over it, so it cannot be
  forged without the signing key or replayed after expiry (30 days)
- the signing key is generated once and persisted at `$HOFRADAR_DATA_DIR/secret_key`
  with mode 0600, so restarts do not log you out. Set `HOFRADAR_SECRET_KEY`
  explicitly if you ever run more than one instance.
- 8 failed attempts from one client → a 5 minute lockout, so a public URL is
  not a free brute-force oracle
- `/api/*` and HTMX requests get `401` + `HX-Redirect`, never an HTML login page
- `/healthz` stays public for container health checks but returns only
  `{"status": "ok"}` to an anonymous caller — it does not leak how many
  properties you are tracking
- the `next` parameter is validated, so the login form cannot be used as an
  open redirect

What it deliberately does not do: no user accounts, no 2FA, no password reset,
no rate limiting beyond the login form. If this ever needs to be shared with
someone else, that is the point to put a real identity provider in front of it
rather than to grow this file.

## When CI and Pages fail before they start

If a workflow run fails within a few seconds and the job has **no logs at all**,
do not go looking for the bug in the YAML. Check whether a runner was ever
assigned:

```
GET /repos/{owner}/{repo}/actions/jobs/{job_id}
```

`"runner_id": 0`, an empty `runner_name`, no `steps` array, and
`"duration_ms": 0` in the run's usage together mean the job died before
`Set up job` — GitHub never gave it a machine. That is an account-level state,
not a defect in the workflow, and no edit to `ci.yml` or `pages.yml` will change
it. Both workflows fail identically while it holds.

Where to look, in order:

1. <https://github.com/settings/billing> — a spending limit or a failed payment
   method. Actions minutes are free for public repositories, but a payment
   failure can disable Actions across the whole account, public repos included.
2. **Settings → Actions → General** on the repository, for a policy that
   disables workflows.

Once cleared, re-run the failed run rather than pushing an empty commit. The
steps themselves are known good — they can all be reproduced locally:

```bash
ruff check src tests scripts
python scripts/sync_config_defaults.py && git diff --exit-code src/hofradar/_config_defaults
PYTHONPATH=src python -m pytest -q
```

## Backups

The whole database is one SQLite file. That is a feature:

```bash
docker compose exec hofradar sh -c 'sqlite3 /data/hofradar.sqlite3 ".backup /data/backup.sqlite3"'
docker compose cp hofradar:/data/backup.sqlite3 ./hofradar-backup.sqlite3
```

On Fly: `fly ssh console` then the same, or snapshot the volume
(`fly volumes snapshots list`). Do this before changing the schema — v0.1 has
no migration framework on purpose (see `docs/DECISIONS.md` §10).
