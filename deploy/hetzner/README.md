# Hofradar on a Hetzner VPS

One `cx22` (2 vCPU / 4 GB / 40 GB, ~€4/month, Nuremberg or Falkenstein) runs the
whole thing: the web UI, the weekly crawl, and a TLS proxy in front. The disk is
a real disk, which is the entire point — the database remembering what it has
seen since February *is* the product.

## Files here

| File | What it is |
|---|---|
| `cloud-init.yaml` | Paste into Hetzner's **Cloud config** box at server creation |
| `docker-compose.prod.yml` | Overlay on the repo's `docker-compose.yml`: loopback-only app port, Caddy, log rotation |
| `Caddyfile` | TLS terminator; also the thing that sets `X-Forwarded-Proto` so session cookies get `Secure` |

## Before you boot

Edit three lines in `cloud-init.yaml`:

1. `ssh_authorized_keys` — your public key. Root login and password auth are both
   off, so a placeholder here means a server you cannot log into.
2. `HOFRADAR_DOMAIN` — a hostname that resolves to this server. See *No domain?*
   below if you do not have one. Leave it empty and Caddy serves plain HTTP on
   `:80` instead of requesting a certificate — only acceptable behind an SSH
   tunnel or Tailscale, never for something you open from your phone.
3. `ACME_EMAIL` — where Let's Encrypt sends expiry warnings.

Optionally set `ANTHROPIC_API_KEY` (enables the LLM review stage; everything
else runs without it) and `HOFRADAR_PASSWORD_HASH` (from
`hofradar hash-password`). If you leave the hash empty, first boot generates a
password, hashes it, and writes the plaintext to
`/home/hofradar/INITIAL_PASSWORD.txt` — the gate is never left off, because a
public URL with no gate is exactly what invariant 8 forbids.

If the GitHub repo is private, `git clone` on first boot has no credentials.
Either make the repo public, or add a read-only deploy key as an extra
`write_files` entry at `/home/hofradar/.ssh/id_ed25519` (mode `0600`, owner
`hofradar`) and set `HOFRADAR_REPO` to the `git@github.com:…` URL.

## No domain?

Caddy needs a *name* to get a certificate for, not a registrar receipt. Two ways
to get one without buying anything.

### DuckDNS (free, two minutes)

1. Sign in at [duckdns.org](https://duckdns.org) with any of the offered logins
   and claim a subdomain, e.g. `hofradar` → `hofradar.duckdns.org`.
2. Copy the token shown at the top of the page.
3. In `cloud-init.yaml` set `HOFRADAR_DOMAIN=hofradar.duckdns.org` and
   `DUCKDNS_TOKEN=<that token>`.

First boot then points the record at itself *before* Caddy asks for a
certificate, and a timer refreshes it every six hours — so rebuilding the server
onto a new IP fixes itself instead of silently breaking renewal. Leave the token
empty if you would rather set the IP by hand on the DuckDNS page; the record
still has to be right before the machine boots.

`duckdns.org` sits on the Public Suffix List, so Let's Encrypt counts its rate
limits per *your* subdomain rather than pooling everyone who uses the service —
which is the thing that makes some other free-DNS options unusable.

### Buy one

A `.de` or `.xyz` is €5–10 a year and removes a dependency. Point an A record at
the server's IPv4 (and AAAA at its IPv6), set `HOFRADAR_DOMAIN`, done — no token,
no updater. Switching later is one line in `/opt/hofradar/app/.env` followed by
`sudo systemctl reload hofradar`; Caddy requests the new certificate itself.

### What about Tailscale Funnel?

It does give you a public HTTPS URL on `<host>.<tailnet>.ts.net` with no domain
and no open ports, reachable by people who are not on your tailnet. It is a
different machine shape though — no Caddy, no 80/443, `tailscale funnel` in
front of `127.0.0.1:8000` — and it is `ts.net` only, with bandwidth limits
Tailscale does not publish. Worth it if you want zero exposed ports; ask and
this can be wired as a second cloud-init variant.

## Boot

```bash
hcloud server create --name hofradar --type cx22 --image ubuntu-24.04 \
  --location nbg1 --user-data-from-file deploy/hetzner/cloud-init.yaml
```

or paste the file into the console's *Cloud config* field. First boot takes
about four minutes; most of it is building the image.

```bash
ssh hofradar@<ip>
sudo cloud-init status --wait
sudo tail -n 100 /var/log/hofradar-bootstrap.log
cat ~/INITIAL_PASSWORD.txt      # if you did not supply a hash
```

## What you get

- **Access**: `hofradar` user, key-only SSH, root login disabled, `fail2ban` on
  `sshd`, unattended security upgrades with a 04:30 reboot window.
- **Firewall**: ufw denies inbound except 22 (rate-limited), 80 and 443. Docker
  publishes past ufw, which is precisely why the app binds `127.0.0.1:8000` and
  only Caddy is exposed.
- **TLS**: automatic certificate and renewal, HSTS, HTTP→HTTPS redirect.
- **Persistence**: SQLite on the `hofradar-data` named volume, plus 2 GB of swap
  so the image build does not get OOM-killed on a small machine.
- **Backups**: nightly at 03:20 via `hofradar-backup.timer`, using SQLite's
  online backup API (never a `cp` of a file a crawl may be writing), gzipped
  into `/var/backups/hofradar`, 14 days kept.
- **Scheduler**: the repo's own `scheduler` service, Mondays 06:00 Europe/Berlin.

## Day-to-day

```bash
sudo systemctl status hofradar          # up?
hofradar-update                         # git pull, backup, rebuild, restart
sudo hofradar-set-password              # change the gate password
sudo hofradar-backup                    # backup right now
sudo hofradar-duckdns                   # re-point the DuckDNS record now
sudo systemctl start hofradar-backup    # ...or via the unit
cd /opt/hofradar/app && docker compose \
  -f docker-compose.yml -f deploy/hetzner/docker-compose.prod.yml logs -f hofradar
```

Run a crawl by hand:

```bash
cd /opt/hofradar/app
docker compose -f docker-compose.yml -f deploy/hetzner/docker-compose.prod.yml \
  exec hofradar hofradar run
```

Pull a backup down to your laptop:

```bash
scp hofradar@<ip>:/var/backups/hofradar/hofradar-*.sqlite3.gz .
```

## Things worth knowing

- Configuration lives in `/opt/hofradar/app/.env` (mode 0600). `app.env` in
  `/opt/hofradar` is only the seed for first boot and is not read again.
- `HOFRADAR_SECRET_KEY` is generated once at boot so restarts do not log you out.
- **Schema changes migrate themselves** on the next start, `hofradar-update`
  included — see `docs/DECISIONS.md` §17. It backs the database up first, which
  is the part not to skip.
- Off-site copies are your job. A Hetzner volume snapshot or an `scp` from a
  cron job on your own machine both work; the whole database is one file.
