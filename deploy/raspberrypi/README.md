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

**Check the name while you are here:** `hostname`. Ubuntu Server's image does
not always take the Imager's hostname and comes up as `ubuntu`, and then every
`hofradar.local` in this guide is `ubuntu.local` for you. The bootstrap prints
the addresses it actually found at the end, so trust that over this page.

### Where is your SSD, exactly?

**Find this out before touching anything.** There are two layouts and they need
opposite things, so answer the question rather than assuming:

```bash
findmnt -no SOURCE /     # what the root filesystem is actually on
lsblk -f
```

**Layout A - the Pi boots from the SSD** (an M.2 HAT, or a USB-SATA adapter as
the boot device). `findmnt` says `/dev/sda2`, `/dev/nvme0n1p2` or similar, and
`/boot/firmware` sits on partition 1 of that same disk.

Then you are already done: the whole system, database included, lives on the
SSD. **Mount nothing, and leave `HOFRADAR_DATA_MOUNT` empty.** Do not try to
mount a partition of the boot disk at `/mnt/ssd` - partition 1 is the firmware
partition, not a spare, and pointing an `ext4` fstab entry at that `vfat`
partition earns you an emergency shell on the next boot.

**Layout B - the OS is on the SD card and the SSD is a second disk.**
`findmnt` says `/dev/mmcblk0p2`. This is the layout worth fixing, and the one
`HOFRADAR_DATA_MOUNT` exists for. Identify the SSD in `lsblk -f` by size and by
the fact that it is *not* the disk carrying `/`; the commands below assume that
came out as `sda`, with one partition `sda1`. Substitute what you actually saw.

```bash
sudo mkfs.ext4 /dev/sda1        # DESTROYS /dev/sda1. Only on the blank SSD,
                                # never on a partition that lsblk shows mounted
sudo mkdir -p /mnt/ssd
echo "UUID=$(sudo blkid -s UUID -o value /dev/sda1) /mnt/ssd ext4 defaults,nofail,noatime 0 2" \
  | sudo tee -a /etc/fstab
sudo systemctl daemon-reload
sudo mount -a && df -h /mnt/ssd
```

Then set `HOFRADAR_DATA_MOUNT=/mnt/ssd/hofradar` in step 3.

`noatime` because there is no reason to write a timestamp every time something
is read. `nofail` so a disk that is missing at boot costs you the mount rather
than the whole boot. Mount by UUID, not by `/dev/sda1`: USB device names move
around between boots and a fstab entry that points at the wrong disk is a bad
morning.

**If you already added an fstab line you should not have**, remove it before
rebooting - a failing entry drops Ubuntu into an emergency shell:

```bash
sudo cp /etc/fstab /etc/fstab.bak
grep -n "/mnt/ssd" /etc/fstab            # look at it first
sudo sed -i '\|/mnt/ssd|d' /etc/fstab
sudo systemctl daemon-reload
sudo findmnt --verify --verbose          # no errors = safe to reboot
```

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
- `HOFRADAR_DATA_MOUNT` — only for step 2's **layout B**: the OS on an SD card
  and the database to be moved onto a separate SSD, e.g. `/mnt/ssd/hofradar`.
  **Leave it empty if the Pi boots from the SSD** — the default location is
  already on that disk, and a bind mount would add nothing. This is the one
  setting that is annoying to change later, so get the layout right first.
- `ANTHROPIC_API_KEY` — optional; without it the deterministic pipeline still
  runs end to end and the LLM review stage is skipped.
- `TYPESAFE_API_KEY` — optional; enables the System One (Jev) triage that asks
  every crawled listing whether it is for sale or for rent, farm or flat, and
  keeps the rentals and flats out of the radar. Without it the run log says so.

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

**Moving an existing database from another machine?** Read *Bringing an
existing database with you* below before you run this — seeding the file first
is less work than swapping it afterwards.

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

1. **Get the database onto an SSD.** Best by a distance — even a cheap SSD
   outlives an SD card by years under this load. Booting the Pi from the SSD
   outright (an M.2 HAT) is the cleanest version and needs no configuration at
   all; a second SSD beside an SD-card system is `HOFRADAR_DATA_MOUNT`, step 2.
   If neither applies, this section is about you.
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
2. **Port 443 is forwarded** to the Pi, TCP (and UDP, for HTTP/3). That is all
   Caddy needs: it proves the name to Let's Encrypt over 443 itself
   (TLS-ALPN). Forwarding 80 as well adds the `http://` → `https://` redirect
   and a second way to prove the name, but it is optional, and a router that
   wants 80 for itself can keep it.
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

#### On a FritzBox

Internet → Freigaben → Portfreigaben, the Pi's entry. Three traps, all of them
silent:

- **An external port belongs to exactly one device.** A 443 rule left on an old
  device - even one that is switched off - keeps the port, and the Pi's new
  rule is quietly given some other external port instead. The only sign is a
  yellow triangle on the rule: *„Für diese Freigabe wurden andere Ports extern
  vergeben als von Ihnen gewünscht."* From outside it looks like *connection
  refused*. Look for 443 on every device in the overview, delete the stray one,
  then delete and re-add the Pi's rule: an existing rule is not moved back to
  443 on its own.
- **The box's own remote access can hold 443.** Internet → Freigaben →
  FRITZ!Box-Dienste: if *Internetzugriff über HTTPS* uses 443, move it to a
  high port. MyFRITZ keeps working and its app finds the new port by itself.
- **Pin the Pi's address.** Tick *Diesem Netzwerkgerät immer die gleiche
  IPv4-Adresse zuweisen* on its entry, so the forward and the Pi cannot drift
  apart. A Pi on both Wi-Fi and ethernet shows up twice; the rule belongs on
  the interface that actually has the address.

A FritzBox loops its public address back inside, so once this works the
`https://` name works from home too.

#### Telling where it stops

Test from **outside** - a phone on mobile data, not on your Wi-Fi. What the
request gets back says which hop is missing:

| From outside, `https://<name>/healthz` gives | Meaning |
|---|---|
| *connection refused* | The router is not forwarding 443 to the Pi - see *On a FritzBox*. The Pi cannot cause this: Caddy listens on every interface, and ufw drops rather than refuses. |
| a timeout | The DNS record points at an old address, or something upstream drops the port. `dig +short <name>` against `curl -4 ifconfig.me`. |
| a TLS error (*internal error*) | Caddy is reached and has no certificate yet. It retries on a growing backoff after failures; `sudo hofradar-compose restart caddy` tries now, and `sudo hofradar-compose logs --tail 50 caddy` says why the last try failed. |
| `{"status":"ok"}` | Done. `/` should now send you to the login page. |

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

Pull a backup down to your laptop. `dan@hofradar.local` here and throughout is
the user and hostname from step 1 — substitute your own. If `.local` does not
resolve, the Pi's IP (`hostname -I` on the Pi) always works: mDNS needs
`avahi-daemon`, which Ubuntu Server does not ship and the bootstrap installs.

```bash
scp dan@hofradar.local:/var/backups/hofradar/hofradar-*.sqlite3.gz .
```

Note the direction: this **fetches** the Pi's nightly backups. Sending a
database the other way, from a laptop to a new Pi, is *Bringing an existing
database with you* above.

## Bringing an existing database with you

If you have been running Hofradar on a laptop, the database there is the thing
worth moving: it is the months of memory that let the radar say *„kennen wir
seit Februar"* instead of showing you the same twelve farms again. It is one
file, and three details decide whether it arrives intact.

**Never `cp` the database.** SQLite runs in WAL mode here (`PRAGMA
journal_mode=WAL`, `db/session.py`), so committed rows may still be sitting in
the `hofradar.sqlite3-wal` sidecar. A copy of the main file alone can arrive
quietly short — which is this codebase's favourite failure, silence that looks
like success. `scripts/backup_db.py` uses SQLite's own backup API: consistent
with WAL active, and safe to run while the app is up.

**Only the database travels.** The data directory holds three things:

| File | Travels? |
|---|---|
| `hofradar.sqlite3` | **Yes** — all of it, saved UI settings included: `search_profiles` is a table, not a file |
| `secret_key` | No. The Pi has its own, and `HOFRADAR_SECRET_KEY` in its `.env` wins over the file anyway. You log in once more, that is all. |
| `hofradar.sqlite3.migrate-lock` | No. A lock; it holds no data. |

`config/*.yaml` does not travel either — the Pi gets that from git. **Commit
any local YAML edits first**, or the Pi will run a different search DNA than
your laptop and you will wonder why the scores moved.

**The schema migrates up, never down.** The database carries an Alembic stamp
and the Pi brings it to head on boot. If your laptop is on a branch with a
migration the Pi's checkout has never seen, `ensure_schema` refuses to start
rather than guess. Check before you copy: `git log --oneline -1` on both, and
put the Pi on the same branch if they differ.

### On the laptop

```bash
cd ~/…/real-estate-tracker
hofradar migrate --check          # note the "revision <X>, head <Y>" line
python scripts/backup_db.py       # -> backups/hofradar-<stamp>.db
```

Count what you are carrying, from the snapshot rather than the original — that
checks the snapshot itself, which is the file that is actually travelling:

```bash
python - <<'COUNT'
import sqlite3, glob
snap = sorted(glob.glob("backups/hofradar-*.db"))[-1]
db = sqlite3.connect(snap)
print(snap)
for t in ("properties", "observations", "price_history", "scores", "search_profiles"):
    print(f"  {t:16s}", db.execute(f"select count(*) from {t}").fetchone()[0])
COUNT
scp backups/hofradar-<stamp>.db dan@hofradar.local:/tmp/
```

Keep those numbers. They are the proof at the other end.

### On the Pi, before the first bootstrap

The tidy path: put the file where the database is going to live, then let the
first boot migrate it. Nothing to stop, nothing to swap.

```bash
# docker runtime: uid 10001 is the image's user (see the Dockerfile)
sudo install -d -o 10001 -g 10001 /mnt/ssd/hofradar
sudo install -o 10001 -g 10001 -m 0600 /tmp/hofradar-<stamp>.db \
     /mnt/ssd/hofradar/hofradar.sqlite3

# native runtime: the service user owns it instead
sudo install -d -o hofradar -g hofradar /mnt/ssd/hofradar
sudo install -o hofradar -g hofradar -m 0600 /tmp/hofradar-<stamp>.db \
     /mnt/ssd/hofradar/hofradar.sqlite3
```

Set `HOFRADAR_DATA_MOUNT=/mnt/ssd/hofradar` in `/opt/hofradar/hofradar.env` to
match, then run the bootstrap as in step 4. `init-db` brings the schema current
before `serve` ever starts.

### On the Pi, if it is already running

Stop it first — restoring under a running crawl is how you get a database that
is half one thing and half another.

```bash
sudo systemctl stop hofradar                     # native: add hofradar-scheduler
sudo install -o 10001 -g 10001 -m 0600 /tmp/hofradar-<stamp>.db \
     /mnt/ssd/hofradar/hofradar.sqlite3
sudo rm -f /mnt/ssd/hofradar/hofradar.sqlite3-wal \
           /mnt/ssd/hofradar/hofradar.sqlite3-shm
sudo systemctl start hofradar
```

Do not skip the `rm`. Those sidecars belong to the database you just replaced,
and leaving them beside a different file is a real way to corrupt it.

If you left `HOFRADAR_DATA_MOUNT` empty, the database is in a Docker volume
rather than on a path — use the `docker run --rm -v hofradar_hofradar-data`
recipe in *Restoring a backup* below to get the file in.

### Verify, then decide which machine is real

The paths below assume `HOFRADAR_DATA_MOUNT=/mnt/ssd/hofradar`. If you left it
empty, `grep HOFRADAR_DATA_DIR /opt/hofradar/app/.env` says where the database
actually is — and on the `docker` runtime an empty mount means a Docker volume,
not a path, so use the `docker run --rm -v hofradar_hofradar-data` form from
*Restoring a backup* instead.

```bash
sudo hofradar-cli migrate --check    # "schema is current", exit 0
sudo sqlite3 /mnt/ssd/hofradar/hofradar.sqlite3 \
  "select count(*) from properties; select count(*) from observations;"
sudo hofradar-health
sudo hofradar-backup                 # prove the backup loop works on real data
```

The counts must match the ones from the laptop. If `migrate --check` still
reports pending work after a restart, stop and find out why before adding
anything new — a half-migrated database is the one state worth refusing.

Then **retire the laptop copy**. Invariant 2 — never report a known property as
new — assumes one memory. Keep both running and they diverge silently: each
will call things NEW that the other has known since February, and there is no
merge path back.

## Restoring a backup

Stop the app first. Restoring under a running crawl is how you get a database
that is half one thing and half another.

`native`:

```bash
sudo systemctl stop hofradar hofradar-scheduler
sudo -u hofradar sh -c 'gunzip -c /var/backups/hofradar/hofradar-20260901T032000Z.sqlite3.gz \
  > /var/lib/hofradar/hofradar.sqlite3'
# The sidecars belong to the database you just overwrote - WAL is on, so
# leaving them next to a different file is a way to corrupt it.
sudo rm -f /var/lib/hofradar/hofradar.sqlite3-wal /var/lib/hofradar/hofradar.sqlite3-shm
sudo systemctl start hofradar hofradar-scheduler
```

`docker`, into the named volume:

```bash
gunzip -c /var/backups/hofradar/hofradar-20260901T032000Z.sqlite3.gz > /tmp/restore.sqlite3
sudo systemctl stop hofradar
sudo docker run --rm -v hofradar_hofradar-data:/data -v /tmp:/host alpine \
  sh -c 'cp /host/restore.sqlite3 /data/hofradar.sqlite3 \
      && rm -f /data/hofradar.sqlite3-wal /data/hofradar.sqlite3-shm \
      && chown 10001:10001 /data/hofradar.sqlite3'
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
- **CI does not cover any of this.** The workflow lints and tests the Python
  package; no shell here is linted and no path here is booted by it. This
  folder is written against the Hetzner deployment that does work, and the
  Pi-specific parts — the ARM image build, `dphys-swapfile`, `vcgencmd` — are
  reasoned, not exercised on real hardware. Read the bootstrap before you run
  it; it is commented for exactly that.

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
| Caddy never gets a certificate | 443 does not reach the Pi (*On a FritzBox*), the DNS record points somewhere else, or you are behind CGNAT and it never could. *Telling where it stops* narrows it down. |
| A yellow triangle on the Pi's FritzBox Freigabe | The external port you asked for belongs to another device or to the box itself, so this rule got a different one. See *On a FritzBox*. |
| `hofradar.local` does not resolve, `ubuntu.local` does | The OS kept its default hostname (step 2). Use the name that resolves, or the IP. |
| The scheduler container shows `unhealthy` | On a checkout from before the fix: the image's health check polls the web port, which the scheduler container never opens. It meant nothing. `hofradar-update` picks up the fix. |
| `hofradar-health` shows throttling ≠ `0x0` | Power supply or cable. Not software. |
