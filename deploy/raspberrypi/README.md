# Hofradar on a Raspberry Pi

A Pi in a cupboard is arguably the *best* host for this. The database
remembering what it has seen since February is the product, and here the disk
is a real disk in your own house, on hardware nobody else can suspend for
non-payment. There is no monthly bill and no public attack surface. The whole
thing idles at a couple of watts.

What it costs you is that you are now the datacentre: the power, the SD card,
and the fact that a home internet line is not a server line. Those three
things are what this folder is mostly about.

This mirrors `deploy/hetzner/` step for step — same operator commands, same
password gate, same nightly backup. Where it differs, it says why.

## Files here

| File | What it is |
|---|---|
| `bootstrap.sh` | The Pi's answer to `cloud-init.yaml`: run once over SSH, idempotent, does everything |
| `hofradar.env.example` | The handful of settings you might want to change first |
| `docker-compose.pi.yml` | Overlay on the repo's `docker-compose.yml`: variable bind address, movable database, Caddy behind a profile |
| `Caddyfile` | Optional TLS terminator, only if this Pi is genuinely meant to be public |

## What you need

| | |
|---|---|
| **Pi 5, or Pi 4 with 4 GB+** | Comfortable. Either runtime below. |
| **Pi 4 2 GB, Pi 3B+, Pi Zero 2 W** | Works, but use the `native` runtime — building an ARM Docker image on it is an afternoon. |
| **A 64-bit OS** | Not optional. Raspberry Pi OS Lite (64-bit) or Ubuntu Server 24.04 arm64. On 32-bit, rapidfuzz / pydantic-core / Pillow / uvloop have no wheels, so pip tries to *compile* them. The bootstrap refuses to continue and tells you this. |
| **A USB SSD** | Strongly recommended, not required. See *The SD card question*. |
| **The official power supply** | The classic Pi failure is undervoltage, and it corrupts filesystems and databases. `hofradar-health` reports it. |

## The two runtimes

`HOFRADAR_RUNTIME` in `hofradar.env` picks one. Both end up with a
`hofradar.service` you start, stop and update the same way.

| | `docker` (default) | `native` |
|---|---|---|
| What runs | The same compose project as the Hetzner box | A virtualenv and two systemd units |
| First install | 15–25 min on a Pi 4, longer on a Pi 3 — it builds an ARM image | 2–4 minutes |
| Memory | ~250 MB more | Lower; fine on 2 GB |
| Isolation | Container | Just a service user |
| The CLI | `sudo hofradar-cli …` (into the container) | `sudo hofradar-cli …`, or `/opt/hofradar/venv/bin/hofradar` directly |
| Logs | `sudo hofradar-compose logs -f hofradar` | `journalctl -u hofradar -f` |

Pick `docker` if this Pi is a sibling of the VPS and you want one mental model.
Pick `native` if the Pi is small, or if you like being able to run
`hofradar run --sources zvg_bayern` without a container in the way.

## Step 1 — flash the card

Raspberry Pi Imager already does the parts cloud-init would have done on a VPS,
so let it. **Choose OS → Raspberry Pi OS (other) → Raspberry Pi OS Lite
(64-bit)**, then open the gear / *Edit settings*:

- **Hostname**: `hofradar`. This is what makes `http://hofradar.local:8000`
  work later, so it is not cosmetic.
- **Username**: your own, e.g. `dan`. The bootstrap creates a separate
  unprivileged `hofradar` service user for the app itself.
- **Password**: leave it, but on the *Services* tab enable SSH with
  **public-key only**. The bootstrap does not disable password SSH for you —
  on a LAN that is your call, unlike on a public IP.
- **Wireless LAN** and **locale** (`Europe/Berlin`): fill in if you are not on
  ethernet. Ethernet is the better answer for a machine that crawls for an hour
  once a week.

Write the card, boot the Pi, wait a minute.

## Step 2 — first login

```bash
ssh dan@hofradar.local          # or the IP from your router's DHCP list
uname -m                        # must say aarch64
```

If `uname -m` says `armv7l`, stop and reflash with the 64-bit image.

**If you have an SSD, mount it now**, before the bootstrap, so the database is
born in the right place:

```bash
lsblk                                        # find it, e.g. sda1
sudo mkfs.ext4 /dev/sda1                     # ONLY if it is a blank disk
sudo mkdir -p /mnt/ssd
echo "UUID=$(sudo blkid -s UUID -o value /dev/sda1) /mnt/ssd ext4 defaults,noatime 0 2" \
  | sudo tee -a /etc/fstab
sudo mount -a && df -h /mnt/ssd
```

`noatime` because there is no reason to write a timestamp every time something
is read. Mount by UUID, not by `/dev/sda1`: USB device names move around
between boots and a fstab entry that points at the wrong disk is a bad morning.

## Step 3 — the source and the settings

```bash
sudo apt-get update && sudo apt-get install -y git
sudo git clone https://github.com/DanM3rcurius/real-estate-tracker.git /opt/hofradar/app
sudo install -m 0600 /opt/hofradar/app/deploy/raspberrypi/hofradar.env.example \
                     /opt/hofradar/hofradar.env
sudo nano /opt/hofradar/hofradar.env
```

Every value in there has a working default; read the comments and change what
you care about. The ones that actually matter on day one:

- `HOFRADAR_RUNTIME` — `docker` or `native`, per the table above.
- `HOFRADAR_DATA_MOUNT` — set to `/mnt/ssd/hofradar` if you did step 2's SSD
  part. This is the one setting that is annoying to change later.
- `ANTHROPIC_API_KEY` — optional; without it the deterministic pipeline still
  runs end to end and the LLM review stage is skipped.

You do **not** need a domain, a certificate, a port forward or a password —
the bootstrap generates the password and leaves it in a file.

## Step 4 — run the bootstrap

```bash
sudo bash /opt/hofradar/app/deploy/raspberrypi/bootstrap.sh
```

It is idempotent: edit `hofradar.env` and run it again, or run it again after a
failure, and it converges instead of duplicating itself. It will not rotate
your cookie signing key or reset your password on a re-run. Everything lands in
`/var/log/hofradar-bootstrap.log`.

On the `docker` runtime, the image build is most of the wall-clock time. Go and
do something else; `tail -f /var/log/hofradar-bootstrap.log` if you want to
watch.

## Step 5 — open it

The last thing the bootstrap prints is the URL and where the password is.

```bash
sudo cat /home/hofradar/INITIAL_PASSWORD.txt
sudo hofradar-set-password        # when you want one you can remember
sudo hofradar-health              # is the Pi happy?
```

Then `http://hofradar.local:8000/` from anything on the same network.

## The SD card question

An SD card is a consumable, and a database is the worst thing you can put on
one: small, frequent, aligned writes are exactly the pattern that wears flash
out. A card that dies takes the memory — *the product* — with it.

Three defences, in order of how much they help:

1. **Put the database on a USB SSD** (`HOFRADAR_DATA_MOUNT`, step 2). Best.
   Even a cheap SSD outlives an SD card by years under this load.
2. **Take the backups off the Pi.** Set `HOFRADAR_BACKUP_RSYNC_TARGET` to a NAS
   or another machine — `user@nas:/volume1/backup/hofradar/` — with an SSH key
   in `/home/hofradar/.ssh`. A backup that only exists on the Pi does not
   survive the Pi being stolen, dropped, or cooked by its own power supply.
3. **The bootstrap caps the writes it can see**: Docker's json logs at 10 MB × 5
   and the journal at 200 MB. It does not stop you swapping to the card, which
   is why the small-Pi advice is the native runtime rather than a bigger swap.

If the card does die: reflash, re-run the bootstrap, drop the newest backup
into place (*Restoring a backup*, below). That is the whole disaster recovery
plan, and it is worth rehearsing once while nothing is on fire.

## Reaching it from outside the house

### Default: the LAN, and nothing else

`http://hofradar.local:8000/`, password gate on, nothing exposed to the
internet. For a personal research tool this is the correct answer and you can
stop reading here.

`.local` is mDNS. It works from macOS, Linux, iOS and Windows 10+; some Android
versions still do not resolve it, in which case use the IP (give the Pi a DHCP
reservation in the router so it stops moving).

### Recommended: Tailscale

A private tailnet gets you the Pi from your phone, anywhere, with **no open
ports, no certificate and no dynamic DNS**. Set `HOFRADAR_TAILSCALE=1` before
the bootstrap, or afterwards:

```bash
sudo tailscale up --hostname hofradar
sudo tailscale serve --bg 8000        # https://hofradar.<tailnet>.ts.net
```

`tailscale serve` terminates TLS for you. Set
`HOFRADAR_FORCE_SECURE_COOKIES=1` in `/opt/hofradar/app/.env` and restart, so
the session cookie is marked `Secure` even if the proxy's forwarded headers do
not reach the app (`docs/DEPLOY.md`, *The password gate*).

Keep the password gate on anyway. Tailscale controls who can reach the port;
the gate controls who can read your search history if a device on the tailnet
is lost.

### Public, with a real certificate: `HOFRADAR_PROXY=caddy`

Only if you have decided you want a URL you can send to someone. Three things
must all be true, and on a German home line the first one frequently is not:

1. **Your line has a routable IPv4 address.** Most Telekom and Vodafone
   connections are DS-Lite: IPv6 native, IPv4 shared through the ISP's
   carrier-grade NAT. There is no inbound IPv4 to forward, and no router
   setting changes that — you would need to ask the ISP for a dual-stack line,
   or serve on IPv6 only (which many mobile networks can reach, and many
   corporate ones cannot). Check by comparing the WAN address in your router's
   status page with `curl -4 ifconfig.me`: if they differ, you are behind CGNAT.
2. **Ports 80 and 443 are forwarded** to the Pi, and 80 stays open — that is
   where the ACME challenge lands.
3. **A name points at you.** A `*.duckdns.org` subdomain is free; set
   `HOFRADAR_DOMAIN` and `DUCKDNS_TOKEN` and the bootstrap installs a timer that
   re-points the record every six hours, which matters because a home IP changes
   whenever the router reconnects. A bought domain works too — then keep its
   record updated some other way, or accept the outage after a reconnect.

Then `HOFRADAR_PROXY=caddy`, re-run the bootstrap, and Caddy gets the
certificate itself. The app is bound to `127.0.0.1` in this mode; Caddy is the
only thing listening publicly.

**Do not port-forward 8000 straight to the app instead.** That is plain HTTP:
the password crosses the internet in clear text and so does every session
cookie.

## What you get

- **Access**: an unprivileged `hofradar` service user owning `/opt/hofradar`;
  the app never runs as you or as root. `fail2ban` on `sshd`.
- **Firewall**: ufw, default deny inbound, SSH rate-limited (it reads the port
  out of your `sshd_config`), mDNS allowed, and the app's port opened to
  RFC1918 ranges only. Existing rules are left alone.
  *Caveat, same as on the VPS*: Docker publishes ports past ufw. With the
  docker runtime the ufw rule documents intent rather than enforcing it —
  anything that can route to the Pi reaches the app. That is fine on a LAN, and
  it is the reason the gate is never left off.
- **Persistence**: SQLite in a named volume, on the SSD you pointed at, or on
  the card — your choice, one variable.
- **Backups**: nightly at 03:20 via `hofradar-backup.timer`, using SQLite's
  online backup API (never a `cp` of a file a crawl may be writing), gzipped
  into `/var/backups/hofradar`, 14 days kept, optionally rsynced off the Pi.
  `Persistent=true`, so a backup missed while the Pi was unplugged runs at the
  next boot instead of being skipped.
- **Scheduler**: the pipeline runs Mondays 06:00 Europe/Berlin —
  `HOFRADAR_SCHEDULE_CRON`.
- **Updates**: unattended security upgrades with a 04:30 reboot window, and
  `hofradar-update` for the app itself.
- **The gate**: invariant 8. No hash configured means one is generated, never
  that the gate is skipped.

## Day-to-day

```bash
sudo hofradar-health            # units, /healthz, temperature, throttling, disk, backups
sudo hofradar-update            # backup, git pull, rebuild/reinstall, restart
sudo hofradar-backup            # a backup right now
sudo hofradar-set-password      # change the gate password
systemctl status hofradar
```

The CLI is the same sentence on both runtimes — `hofradar-cli` runs it as the
service user, from the project directory, with the service's environment:

```bash
sudo hofradar-cli run                      # one full pipeline run, now
sudo hofradar-cli run --sources zvg_bayern
sudo hofradar-cli migrate --check          # exit 1 while a migration is pending
sudo hofradar-cli config                   # the resolved search profile
```

Do not reach past it with a bare `hofradar run`: without the service's
environment the CLI resolves `HOFRADAR_DATA_DIR` to `./data` and quietly opens
a second, empty database next to the real one.

Logs differ, because that is genuinely two different mechanisms:

```bash
sudo hofradar-compose logs -f hofradar     # docker runtime
journalctl -u hofradar -f                  # native runtime
journalctl -u hofradar-scheduler -f        # native: the weekly crawl
```

Pull a backup down to your laptop:

```bash
scp dan@hofradar.local:/var/backups/hofradar/hofradar-*.sqlite3.gz .
```

## Restoring a backup

Stop the app first. Restoring under a running crawl is how you get a database
that is half one thing and half another.

`native`:

```bash
sudo systemctl stop hofradar hofradar-scheduler
sudo -u hofradar sh -c 'gunzip -c /var/backups/hofradar/hofradar-20260901T032000Z.sqlite3.gz \
  > /var/lib/hofradar/hofradar.sqlite3'
sudo systemctl start hofradar hofradar-scheduler
```

`docker`, into the named volume:

```bash
gunzip -c /var/backups/hofradar/hofradar-20260901T032000Z.sqlite3.gz > /tmp/restore.sqlite3
sudo systemctl stop hofradar
sudo docker run --rm -v hofradar_hofradar-data:/data -v /tmp:/host alpine \
  sh -c 'cp /host/restore.sqlite3 /data/hofradar.sqlite3 && chown 10001:10001 /data/hofradar.sqlite3'
sudo systemctl start hofradar
```

A restored database that predates the code is migrated on the next start
(`docs/DECISIONS.md` §17) — including one old enough to predate Alembic.

## Things worth knowing

- **Undervoltage is the Pi's signature failure.** A marginal supply or a long
  USB cable causes silent throttling, corrupt writes and inexplicable SQLite
  errors. `hofradar-health` prints `vcgencmd get_throttled`; anything but `0x0`
  means fix the power before you debug the software.
- **A Pi has no real-time clock.** After a power cut it comes up believing
  whatever the filesystem last thought, until NTP corrects it. The backup timer
  is `Persistent=true` and the scheduler is cron-shaped, so both recover on
  their own — but a run logged at a strange hour after an outage is this, not a
  bug.
- **Power cuts are the one thing an SSD does not fix.** SQLite survives them by
  design; SD cards frequently do not. A £30 UPS HAT or a small UPS is a
  reasonable purchase for a box whose entire job is remembering things.
- **The weekly run is the only heavy moment.** A crawl walks sitemaps, geocodes
  and routes; on a 2 GB Pi that is when swap gets used. If the Pi is also doing
  something else at 06:00 on Mondays, move `HOFRADAR_SCHEDULE_CRON`.
- **Nominatim and OSRM are public services** being used politely by default.
  A Pi crawling weekly is well inside that, but the endpoints are configurable
  (`.env.example`) if you ever run your own.
- **CI has never actually run in this repo** (see `CLAUDE.md`), so a red check
  on a PR is not evidence of anything. Local green is the verification that
  exists. This folder is no exception: it is written against the Hetzner
  deployment that does work, and the Pi-specific paths — the ARM image build,
  `dphys-swapfile`, `vcgencmd` — have not been exercised on real hardware.
  Read the bootstrap before you run it; it is commented for exactly that.

## Troubleshooting

| Symptom | What it usually is |
|---|---|
| `ssh: Could not resolve hostname hofradar.local` | mDNS not available on your client, or `avahi-daemon` not running. Use the IP; give the Pi a DHCP reservation. |
| Bootstrap stops at `this is a 32-bit system` | Correct behaviour. Reflash with the 64-bit image. |
| `pip install failed` while compiling a wheel | `sudo apt-get install -y build-essential libjpeg-dev zlib1g-dev`, then re-run the bootstrap. |
| The image build is killed | Out of memory. Raise swap, or switch to `HOFRADAR_RUNTIME=native` and re-run. |
| App up, but not reachable from your phone | Bound to loopback (`HOFRADAR_BIND_ADDR`), or `HOFRADAR_PROXY=caddy` which forces loopback, or your phone is on the guest network. |
| „Die Datenbank passt nicht zum Programm" in the UI | The schema is behind the code. `hofradar migrate` — see *Day-to-day*. It normally happens on start by itself. |
| Forgot the password | `sudo hofradar-set-password`. |
| Caddy never gets a certificate | Port 80 not forwarded, the DNS record points somewhere else, or you are behind CGNAT and it never could. See *Public, with a real certificate*. |
| `hofradar-health` shows throttling ≠ `0x0` | Power supply or cable. Not software. |
