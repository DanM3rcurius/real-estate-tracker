# Deployment

## Short version

| Where | Works? | Why |
|---|---|---|
| **Your own machine + Tailscale** | ✅ best fit | Free, private, no public attack surface, disk is real |
| **Fly.io** | ✅ recommended cloud | Persistent volume, always-on process, `fly.toml` is already in the repo |
| **Hetzner VPS** | ✅ most control | A real disk and a real cron for ~€4/month; `deploy/hetzner/cloud-init.yaml` builds the box unattended |
| **Railway / Render** | ✅ browser-only path | Deploy straight from GitHub, add a persistent disk, no CLI needed |
| **Vercel** | ❌ wrong shape | No persistent disk, no long-running process, no real scheduler |
| **Netlify / Cloudflare Pages** | ❌ same reasons | Same serverless model |

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

**No.** Two of the good options are fully browser-driven, and a third builds
itself from a pasted file.

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

### Option D — a Hetzner VPS, built unattended from cloud-init

The most control and the most disk per euro, at the cost of owning a machine.
`deploy/hetzner/cloud-init.yaml` does the whole build: Docker, ufw, fail2ban,
unattended upgrades, Caddy with an automatic certificate, the compose project as
a systemd unit, and a nightly SQLite backup with 14 days of retention.

```bash
# edit three lines first: your SSH key, HOFRADAR_DOMAIN, ACME_EMAIL
hcloud server create --name hofradar --type cx22 --image ubuntu-24.04 \
  --location nbg1 --user-data-from-file deploy/hetzner/cloud-init.yaml
```

A `cx22` (2 vCPU, 4 GB, 40 GB) is comfortable. The app is published on
`127.0.0.1:8000` only and Caddy is the sole thing listening publicly — Docker
writes its own iptables rules and would otherwise punch straight through ufw.

If you supply no `HOFRADAR_PASSWORD_HASH`, first boot mints a password and
leaves it in `/home/hofradar/INITIAL_PASSWORD.txt`; the gate is never simply
absent on a public IP. Details, day-to-day commands and the private-repo case
are in `deploy/hetzner/README.md`.

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

## Backups

The whole database is one SQLite file. That is a feature:

```bash
docker compose exec hofradar sh -c 'sqlite3 /data/hofradar.sqlite3 ".backup /data/backup.sqlite3"'
docker compose cp hofradar:/data/backup.sqlite3 ./hofradar-backup.sqlite3
```

On the Hetzner box this already runs nightly — `hofradar-backup`, landing in
`/var/backups/hofradar`.

On Fly: `fly ssh console` then the same, or snapshot the volume
(`fly volumes snapshots list`). Do this before changing the schema.

## Schema migrations

The container migrates itself. `hofradar init-db`, which the image runs before
`hofradar serve`, brings the database to the current schema first — including a
database old enough to predate Alembic, which is adopted and then upgraded. A
plain `docker compose up -d --build` is therefore all a schema change needs.

Take a backup first anyway (above). `render_as_batch` makes SQLite rebuild the
whole table, and a rebuild is not a thing to have no copy of.

To look before you leap, or to migrate without starting the app:

```bash
docker compose run --rm --no-deps hofradar hofradar migrate --check   # exits 1 if pending
docker compose run --rm --no-deps hofradar hofradar migrate
```

If the UI shows *„Die Datenbank passt nicht zum Programm“*, the schema is behind
the code — that is what these commands fix.

Both containers migrate on start and take a lock first, so bringing them up
together is safe; only one does the work. The `hofradar.sqlite3.migrate-lock`
file beside the database is that lock — it holds no data.
