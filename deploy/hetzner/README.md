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
2. `HOFRADAR_DOMAIN` — a hostname whose A/AAAA record already points at the
   server's IP. Leave it empty and Caddy serves plain HTTP on `:80` instead of
   requesting a certificate; only do that if you reach the box over an SSH
   tunnel or Tailscale.
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
- **Take a backup before pulling a schema change.** v0.1 has no migration
  framework on purpose — see `docs/DECISIONS.md` §10.
- Off-site copies are your job. A Hetzner volume snapshot or an `scp` from a
  cron job on your own machine both work; the whole database is one file.
