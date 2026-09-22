#!/usr/bin/env bash
# Hofradar on a Raspberry Pi at home, from a fresh install.
#
# Raspberry Pi OS has no cloud-init. Where the Hetzner box builds itself from
# deploy/hetzner/cloud-init.yaml before you ever log in, a Pi is flashed by
# Raspberry Pi Imager - which already does the user, the SSH key, the hostname
# and the wifi - and everything after that is this script. The list of concerns
# is the same one: a service user, Docker or a virtualenv, a firewall, the
# source, the password gate, a systemd unit, a nightly backup, unattended
# security upgrades.
#
#   sudo bash deploy/raspberrypi/bootstrap.sh [/path/to/hofradar.env]
#
# It is idempotent. Re-run it after editing the env file and it converges;
# re-run it after a failure and it picks up where it stopped. Everything is
# logged to /var/log/hofradar-bootstrap.log.
#
# What it will NOT do, deliberately:
#   - open a port on your router. See the README; that is a decision, not a step.
#   - leave the UI ungated. Invariant 8: no hash configured means one is
#     generated here, never that the gate is skipped.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${1:-/opt/hofradar/hofradar.env}"
APP_DIR=/opt/hofradar/app
SERVICE_USER=hofradar
VENV_DIR=/opt/hofradar/venv

log()  { printf '\n== %s\n' "$*"; }
warn() { printf '\n!! %s\n' "$*" >&2; }
die()  { printf '\nFATAL: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run this with sudo: sudo bash $0"

install -d -m 0755 /opt/hofradar
exec > >(tee -a /var/log/hofradar-bootstrap.log) 2>&1
echo "=== hofradar bootstrap $(date -Is) ==="

# ---------------------------------------------------------------------------
# Preflight. Two of these are the difference between a working afternoon and a
# confusing one, so they are checked before anything is installed.
# ---------------------------------------------------------------------------
log "preflight"

arch="$(dpkg --print-architecture)"
case "$arch" in
  arm64|amd64) echo "architecture: $arch" ;;
  armhf|armel)
    die "this is a 32-bit ($arch) system. Hofradar's dependencies - rapidfuzz,
     pydantic-core, Pillow, uvloop - publish no wheels for 32-bit ARM, so pip
     would try to compile them and take an hour or fail outright. Reflash with
     the 64-bit image: Raspberry Pi Imager > Raspberry Pi OS (other) >
     Raspberry Pi OS Lite (64-bit). A Pi 3 or newer can run it." ;;
  *) warn "untested architecture '$arch' - continuing, but expect surprises" ;;
esac

. /etc/os-release
case "${ID:-}" in
  debian|raspbian|ubuntu) echo "os: ${PRETTY_NAME:-$ID} (${VERSION_CODENAME:-unknown})" ;;
  *) warn "untested distribution '${ID:-?}' - this script assumes apt and systemd" ;;
esac
command -v systemctl >/dev/null || die "no systemd; this script cannot manage services here"

mem_mb="$(awk '/^MemTotal:/ {print int($2/1024)}' /proc/meminfo)"
echo "memory: ${mem_mb} MB"

root_src="$(findmnt -no SOURCE / 2>/dev/null || echo unknown)"
echo "root filesystem: $root_src"
ROOT_ON_SD=0
case "$root_src" in /dev/mmcblk*) ROOT_ON_SD=1 ;; esac

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
log "configuration"

if [ ! -f "$ENV_FILE" ]; then
  if [ -f "$SCRIPT_DIR/hofradar.env.example" ] && [ "$ENV_FILE" = /opt/hofradar/hofradar.env ]; then
    install -m 0600 "$SCRIPT_DIR/hofradar.env.example" "$ENV_FILE"
    echo "seeded $ENV_FILE from the example - the defaults are usable as they are"
  else
    die "no such file: $ENV_FILE"
  fi
fi
# Sourced by bash: a value with spaces must be quoted in the file.
set -a; . "$ENV_FILE"; set +a

RUNTIME="${HOFRADAR_RUNTIME:-docker}"
case "$RUNTIME" in docker|native) ;; *) die "HOFRADAR_RUNTIME must be docker or native, not '$RUNTIME'" ;; esac
PROXY="${HOFRADAR_PROXY:-none}"
case "$PROXY" in none|caddy) ;; *) die "HOFRADAR_PROXY must be none or caddy, not '$PROXY'" ;; esac
[ "$PROXY" = caddy ] && [ "$RUNTIME" = native ] && \
  die "HOFRADAR_PROXY=caddy is only wired for the docker runtime; on native, put a
     reverse proxy in front of 127.0.0.1:${HOFRADAR_PORT:-8000} yourself"

BIND_ADDR="${HOFRADAR_BIND_ADDR:-0.0.0.0}"
[ "$PROXY" = caddy ] && BIND_ADDR=127.0.0.1
PORT="${HOFRADAR_PORT:-8000}"
BACKUP_DIR="${HOFRADAR_BACKUP_DIR:-/var/backups/hofradar}"
DATA_MOUNT="${HOFRADAR_DATA_MOUNT:-}"
REPO="${HOFRADAR_REPO:-https://github.com/DanM3rcurius/real-estate-tracker.git}"
BRANCH="${HOFRADAR_BRANCH:-trunk}"

echo "runtime=$RUNTIME proxy=$PROXY bind=${BIND_ADDR}:${PORT} branch=$BRANCH"

if [ "$RUNTIME" = docker ] && [ "$mem_mb" -lt 3000 ]; then
  warn "${mem_mb} MB of RAM with the docker runtime. The first image build will
     lean hard on swap and take a while. HOFRADAR_RUNTIME=native installs in a
     few minutes and runs in a fraction of the memory - consider it."
fi

if [ "$ROOT_ON_SD" = 1 ] && [ -z "$DATA_MOUNT" ]; then
  warn "the database will live on the SD card. SD cards wear out under the
     write pattern a database has, and the database remembering what it has
     seen IS the product. If you have a USB SSD, mount it and set
     HOFRADAR_DATA_MOUNT=/mnt/ssd/hofradar in $ENV_FILE, then re-run this.
     Backups (see below) are the fallback, not a substitute."
fi

# ---------------------------------------------------------------------------
# Base packages
# ---------------------------------------------------------------------------
log "packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl git gnupg jq sqlite3 rsync \
  ufw fail2ban unattended-upgrades avahi-daemon
# avahi is what makes http://<hostname>.local resolve on the LAN, which is the
# whole addressing story for a box with no domain.
systemctl enable --now avahi-daemon >/dev/null 2>&1 || true

if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$SERVICE_USER"
  echo "created service user $SERVICE_USER"
fi

# ---------------------------------------------------------------------------
# Swap. The Pi ships with 100-200 MB, which an ARM image build walks straight
# through. Swapping on an SD card is not free either - which is one more reason
# the native runtime is the kinder choice on a small Pi.
# ---------------------------------------------------------------------------
log "swap"
cur_swap_mb="$(awk '/^SwapTotal:/ {print int($2/1024)}' /proc/meminfo)"
if [ "$mem_mb" -ge 4096 ] || [ "$cur_swap_mb" -ge 1024 ]; then
  echo "swap: ${cur_swap_mb} MB, leaving it alone"
elif [ -f /etc/dphys-swapfile ]; then
  sed -i 's/^#\?CONF_SWAPSIZE=.*/CONF_SWAPSIZE=2048/' /etc/dphys-swapfile
  if grep -q '^#\?CONF_MAXSWAP=' /etc/dphys-swapfile; then
    sed -i 's/^#\?CONF_MAXSWAP=.*/CONF_MAXSWAP=2048/' /etc/dphys-swapfile
  else
    echo 'CONF_MAXSWAP=2048' >> /etc/dphys-swapfile
  fi
  dphys-swapfile swapoff || true
  dphys-swapfile setup
  dphys-swapfile swapon
  echo "swap: raised to 2048 MB via dphys-swapfile"
elif [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "swap: created /swapfile (2 GB)"
fi

# ---------------------------------------------------------------------------
# The runtime itself
# ---------------------------------------------------------------------------
if [ "$RUNTIME" = docker ]; then
  log "docker"
  if ! command -v docker >/dev/null 2>&1; then
    case "${ID:-debian}" in ubuntu) docker_repo=ubuntu ;; *) docker_repo=debian ;; esac
    docker_codename="${VERSION_CODENAME:-bookworm}"
    # Raspberry Pi OS occasionally runs ahead of what Docker publishes. Check
    # before adding a source that would break every apt-get update from here on.
    if ! curl -fsSL -o /dev/null \
        "https://download.docker.com/linux/${docker_repo}/dists/${docker_codename}/Release"; then
      warn "Docker publishes nothing for ${docker_repo}/${docker_codename}; using bookworm packages"
      docker_codename=bookworm
    fi
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/${docker_repo}/gpg" \
      | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/%s %s stable\n' \
      "$arch" "$docker_repo" "$docker_codename" > /etc/apt/sources.list.d/docker.list
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io \
      docker-buildx-plugin docker-compose-plugin
  fi
  # Do not clobber a daemon.json somebody already tuned; the log caps also live
  # in the compose overlay, so this file is belt and braces.
  if [ ! -f /etc/docker/daemon.json ]; then
    install -d /etc/docker
    cat > /etc/docker/daemon.json <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "5" },
  "live-restore": true
}
JSON
    systemctl restart docker 2>/dev/null || true
  fi
  systemctl enable --now docker
  usermod -aG docker "$SERVICE_USER"
else
  log "python"
  apt-get install -y --no-install-recommends python3-venv python3-dev
fi
apt-get install -y --no-install-recommends openssl

# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------
log "source"
if [ ! -d "$APP_DIR/.git" ]; then
  git clone --branch "$BRANCH" "$REPO" "$APP_DIR"
  echo "cloned $REPO ($BRANCH)"
fi
# Before any git call as the service user: git refuses to work in a repository
# owned by somebody else, and a clone you made with sudo is owned by root.
chown -R "$SERVICE_USER:$SERVICE_USER" /opt/hofradar
sudo -u "$SERVICE_USER" git -C "$APP_DIR" fetch --quiet origin "$BRANCH" || \
  warn "could not fetch origin/$BRANCH - continuing with the checkout on disk"
# A checkout on a different branch than the one configured is the kind of quiet
# mismatch that has you debugging code you are not running.
on_branch="$(sudo -u "$SERVICE_USER" git -C "$APP_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
if [ "$on_branch" != "$BRANCH" ]; then
  warn "$APP_DIR is checked out on '$on_branch', but HOFRADAR_BRANCH says '$BRANCH'.
     Nothing here switches branches for you: git -C $APP_DIR checkout $BRANCH"
fi

# Where the database lives. In the docker runtime an empty HOFRADAR_DATA_MOUNT
# means a named volume and there is no host directory to create; a path is
# bind-mounted and must be owned by the image's uid (10001, see the Dockerfile).
log "storage"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$BACKUP_DIR"
if [ "$RUNTIME" = docker ]; then
  install -d -o 10001 -g 10001 "$APP_DIR/reports"
  [ -n "$DATA_MOUNT" ] && install -d -o 10001 -g 10001 "$DATA_MOUNT"
  NATIVE_DATA_DIR=""
else
  NATIVE_DATA_DIR="${DATA_MOUNT:-/var/lib/hofradar}"
  install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$NATIVE_DATA_DIR" "$APP_DIR/reports"
fi
if [ -n "$DATA_MOUNT" ]; then
  mount_of_data="$(findmnt -no TARGET -T "$DATA_MOUNT" 2>/dev/null || echo /)"
  if [ "$mount_of_data" = / ] && [ "$ROOT_ON_SD" = 1 ]; then
    warn "HOFRADAR_DATA_MOUNT=$DATA_MOUNT is on the same filesystem as / - the
     disk you meant is not mounted, so the database would go on the SD card
     anyway. Mount it (see the README, step 2) and re-run this."
  fi
fi
echo "backups -> $BACKUP_DIR"
echo "database -> ${DATA_MOUNT:-${NATIVE_DATA_DIR:-docker volume hofradar-data}}"

# ---------------------------------------------------------------------------
# The virtualenv, for the native runtime
# ---------------------------------------------------------------------------
if [ "$RUNTIME" = native ]; then
  log "virtualenv"
  [ -x "$VENV_DIR/bin/python" ] || sudo -u "$SERVICE_USER" python3 -m venv "$VENV_DIR"
  sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install --upgrade --quiet pip
  # Editable, so `git pull` alone is a code update and hofradar-update only has
  # to reinstall when the dependencies actually moved.
  ( cd "$APP_DIR" && sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install --quiet -e ".[pdf,images]" ) \
    || die "pip install failed. If it died compiling a wheel, install the
     toolchain and re-run: apt-get install -y build-essential libjpeg-dev zlib1g-dev"
  "$VENV_DIR/bin/hofradar" --help >/dev/null || die "the hofradar CLI did not install"
  echo "virtualenv ready: $VENV_DIR"
fi

# ---------------------------------------------------------------------------
# .env - read by docker compose, or by systemd as an EnvironmentFile.
# Values already on disk win over regenerated ones: re-running this script must
# not rotate the cookie signing key and log you out, or reset your password.
# ---------------------------------------------------------------------------
log "app configuration"
existing_val() {
  [ -f "$APP_DIR/.env" ] || return 0
  sed -n "s/^$1=//p" "$APP_DIR/.env" | tail -n1 | sed "s/^'\(.*\)'\$/\1/"
}
secret_key="$(existing_val HOFRADAR_SECRET_KEY)"
[ -n "$secret_key" ] || secret_key="$(openssl rand -hex 32)"
pw_hash="${HOFRADAR_PASSWORD_HASH:-}"
[ -n "$pw_hash" ] || pw_hash="$(existing_val HOFRADAR_PASSWORD_HASH)"

# A hash is pbkdf2_sha256$rounds$salt$digest, and both compose (reading .env)
# and bash (sourcing hofradar.env) expand $NAME in an unquoted value: a salt or
# digest that starts with a letter is read as an unset variable and becomes
# nothing. The app then holds a malformed hash and refuses every password, and
# nothing says why. So the docker .env single-quotes it (existing_val strips the
# quotes again), and a hash that has already lost a part stops us here. The
# native runtime's readers - systemd and hofradar-cli - expand nothing, and
# hofradar-cli would pass quotes through literally, so there it stays bare.
HASH_RE='^pbkdf2_sha256\$[0-9]+\$[^$]+\$[0-9a-f]{64}$'
if [ -n "$pw_hash" ] && ! [[ "$pw_hash" =~ $HASH_RE ]]; then
  die "HOFRADAR_PASSWORD_HASH is not a whole pbkdf2 hash: '${pw_hash}'.
     In ${ENV_FILE} it must be single-quoted, or bash eats every \$part:
       HOFRADAR_PASSWORD_HASH='pbkdf2_sha256\$240000\$...'
     Or leave it empty there and use: sudo hofradar-set-password"
fi
hash_q=""
[ "$RUNTIME" = docker ] && hash_q="'"

# Invariant 8: no hash anywhere means we mint a password here rather than serve
# an ungated UI - even on a LAN, where the guest wifi is one bad QR code away.
generated_pw=""
if [ -z "$pw_hash" ]; then
  generated_pw="$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)"
fi

umask 077
# Both heredocs below are unquoted so the values expand - which means a backtick
# or $( in one of their comments is *run*, and its output lands in .env. That is
# how `docker volume ls` broke every re-run once the volume existed.
if [ "$RUNTIME" = docker ]; then
  compose_profiles=""
  [ "$PROXY" = caddy ] && compose_profiles=proxy
  cat > "$APP_DIR/.env" <<ENV
# Pinned, so the volume is hofradar_hofradar-data rather than app_hofradar-data
# (compose would otherwise name the project after the directory, and /opt/
# hofradar/app is a poor name to find in "docker volume ls" a year from now).
COMPOSE_PROJECT_NAME=hofradar
HOFRADAR_DATA_DIR=/data
HOFRADAR_DATA_MOUNT=${DATA_MOUNT}
HOFRADAR_BIND_ADDR=${BIND_ADDR}
HOFRADAR_PORT=${PORT}
HOFRADAR_DOMAIN=${HOFRADAR_DOMAIN:-}
ACME_EMAIL=${ACME_EMAIL:-}
COMPOSE_PROFILES=${compose_profiles}
HOFRADAR_SECRET_KEY=${secret_key}
HOFRADAR_PASSWORD_HASH=${hash_q}${pw_hash}${hash_q}
HOFRADAR_FORCE_SECURE_COOKIES=${HOFRADAR_FORCE_SECURE_COOKIES:-}
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
HOFRADAR_LLM_MODEL=${HOFRADAR_LLM_MODEL:-claude-sonnet-5}
TYPESAFE_API_KEY=${TYPESAFE_API_KEY:-}
HOFRADAR_JEV_MODEL=${HOFRADAR_JEV_MODEL:-jev-latest}
HOFRADAR_SCHEDULE_CRON=${HOFRADAR_SCHEDULE_CRON:-0 6 * * 1}
TZ=${TZ:-Europe/Berlin}
ENV
else
  cat > "$APP_DIR/.env" <<ENV
HOFRADAR_DATA_DIR=${NATIVE_DATA_DIR}
HOFRADAR_CONFIG_DIR=${APP_DIR}/config
HOFRADAR_SECRET_KEY=${secret_key}
HOFRADAR_PASSWORD_HASH=${pw_hash}
HOFRADAR_FORCE_SECURE_COOKIES=${HOFRADAR_FORCE_SECURE_COOKIES:-}
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
HOFRADAR_LLM_MODEL=${HOFRADAR_LLM_MODEL:-claude-sonnet-5}
TYPESAFE_API_KEY=${TYPESAFE_API_KEY:-}
HOFRADAR_JEV_MODEL=${HOFRADAR_JEV_MODEL:-jev-latest}
HOFRADAR_SCHEDULE_CRON=${HOFRADAR_SCHEDULE_CRON:-0 6 * * 1}
TZ=${TZ:-Europe/Berlin}
ENV
fi
umask 022
chown "$SERVICE_USER:$SERVICE_USER" "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"
echo "wrote $APP_DIR/.env"

# ---------------------------------------------------------------------------
# Operator scripts. Same names and same jobs as on the Hetzner box, so the
# muscle memory carries over; the difference between the two runtimes is hidden
# behind /etc/hofradar/ops.env and the little shared library below.
# ---------------------------------------------------------------------------
log "operator scripts"
install -d -m 0755 /etc/hofradar
{
  printf '# Written by deploy/raspberrypi/bootstrap.sh. Re-run it to change these.\n'
  printf 'RUNTIME=%q\n'             "$RUNTIME"
  printf 'APP_DIR=%q\n'             "$APP_DIR"
  printf 'VENV_DIR=%q\n'            "$VENV_DIR"
  printf 'SERVICE_USER=%q\n'        "$SERVICE_USER"
  printf 'BACKUP_DIR=%q\n'          "$BACKUP_DIR"
  printf 'BACKUP_RSYNC_TARGET=%q\n' "${HOFRADAR_BACKUP_RSYNC_TARGET:-}"
  printf 'BACKUP_KEEP_DAYS=%q\n'    "${HOFRADAR_BACKUP_KEEP_DAYS:-14}"
  printf 'NATIVE_DATA_DIR=%q\n'     "${NATIVE_DATA_DIR:-}"
  printf 'BIND_ADDR=%q\n'           "$BIND_ADDR"
  printf 'PORT=%q\n'                "$PORT"
} > /etc/hofradar/ops.env
chmod 0644 /etc/hofradar/ops.env

cat > /usr/local/lib/hofradar-common.sh <<'COMMON'
# Sourced by the hofradar-* scripts. Not executable on its own.
set -euo pipefail
. /etc/hofradar/ops.env
COMPOSE_FILES=(-f docker-compose.yml -f deploy/raspberrypi/docker-compose.pi.yml)

need_root() { [ "$(id -u)" -eq 0 ] || { echo "run this with sudo" >&2; exit 1; }; }
# Always from the project directory: compose resolves .env and relative paths
# against the working directory, not against the file it was given.
compose() { ( cd "$APP_DIR" && docker compose "${COMPOSE_FILES[@]}" "$@" ); }
as_service() { if [ "$(id -un)" = "$SERVICE_USER" ]; then "$@"; else sudo -u "$SERVICE_USER" "$@"; fi; }
db_file() { printf '%s/hofradar.sqlite3' "$NATIVE_DATA_DIR"; }
units() { if [ "$RUNTIME" = native ]; then echo "hofradar.service hofradar-scheduler.service"; else echo "hofradar.service"; fi; }
COMMON

cat > /usr/local/bin/hofradar-backup <<'BACKUP'
#!/usr/bin/env bash
# The database remembering what it has seen IS the product, and this one lives
# on a machine in your house. Use SQLite's online backup API rather than
# copying a file a crawl may be writing to, then get a copy off the Pi if a
# target is configured - a backup that only exists on the Pi does not survive
# the Pi.
. /usr/local/lib/hofradar-common.sh

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="${BACKUP_DIR}/hofradar-${stamp}.sqlite3"
mkdir -p "$BACKUP_DIR"

if [ "$RUNTIME" = native ]; then
  src="$(db_file)"
  if [ ! -f "$src" ]; then
    echo "no database at $src yet - nothing to back up"
    exit 0
  fi
  # .backup in the sqlite3 CLI is the same online backup API, and does the
  # right thing while the app holds the file open.
  sqlite3 "$src" ".backup '$out'"
else
  # The container owns /data, so it writes the copy and we lift it out - the
  # archive then belongs to the host user that rotates it.
  compose exec -T hofradar python -c "
import sqlite3
src = sqlite3.connect('/data/hofradar.sqlite3')
dst = sqlite3.connect('/data/.backup-tmp.sqlite3')
src.backup(dst)
dst.close(); src.close()
"
  compose cp hofradar:/data/.backup-tmp.sqlite3 "$out"
  compose exec -T hofradar rm -f /data/.backup-tmp.sqlite3
fi

gzip -f "$out"
find "$BACKUP_DIR" -name 'hofradar-*.sqlite3.gz' -mtime "+${BACKUP_KEEP_DAYS}" -delete
echo "backup ok: ${out}.gz"

if [ -n "${BACKUP_RSYNC_TARGET:-}" ]; then
  if rsync -a --quiet "${out}.gz" "$BACKUP_RSYNC_TARGET"; then
    echo "copied off-box to ${BACKUP_RSYNC_TARGET}"
  else
    # Not fatal: a NAS that is off must not stop the local backup from existing.
    echo "warning: rsync to ${BACKUP_RSYNC_TARGET} failed" >&2
  fi
fi
BACKUP

cat > /usr/local/bin/hofradar-update <<'UPDATE'
#!/usr/bin/env bash
# Pull the branch and restart. The app migrates the schema on start
# (docs/DECISIONS.md 17); taking the backup first is what makes that safe.
. /usr/local/lib/hofradar-common.sh
need_root

/usr/local/bin/hofradar-backup || echo "warning: backup failed, continuing anyway" >&2
as_service git -C "$APP_DIR" pull --ff-only

if [ "$RUNTIME" = native ]; then
  # Editable install, so this is a no-op unless the dependencies moved.
  ( cd "$APP_DIR" && as_service "$VENV_DIR/bin/pip" install --quiet -e ".[pdf,images]" )
  systemctl restart hofradar.service hofradar-scheduler.service
  systemctl --no-pager --lines=0 status hofradar.service hofradar-scheduler.service || true
else
  systemctl reload hofradar.service     # ExecReload is `compose up -d --build`
  docker image prune -f >/dev/null
  compose ps
fi
UPDATE

cat > /usr/local/bin/hofradar-set-password <<'SETPW'
#!/usr/bin/env bash
# Prompts, hashes, writes HOFRADAR_PASSWORD_HASH into .env, restarts.
. /usr/local/lib/hofradar-common.sh
need_root

read -rsp "Passwort: " pw; echo
read -rsp "Wiederholen: " pw2; echo
[ -n "$pw" ] || { echo "no password given" >&2; exit 1; }
[ "$pw" = "$pw2" ] || { echo "passwords do not match" >&2; exit 1; }

# The password goes in through the environment, never as an argument - argv is
# world-readable in /proc while the command runs.
hash_cmd='import os; from hofradar.web.auth import hash_password; print(hash_password(os.environ["HOFRADAR_NEW_PASSWORD"]))'
if [ "$RUNTIME" = native ]; then
  hash="$(HOFRADAR_NEW_PASSWORD="$pw" "$VENV_DIR/bin/python" -c "$hash_cmd")"
else
  hash="$(HOFRADAR_NEW_PASSWORD="$pw" compose run --rm --no-deps -T \
    -e HOFRADAR_NEW_PASSWORD hofradar python -c "$hash_cmd" \
    | grep -o 'pbkdf2_sha256\$[^[:space:]]*' | tail -n1)"
fi
[ -n "$hash" ] || { echo "no hash produced" >&2; exit 1; }

tmp="$(mktemp)"
grep -v '^HOFRADAR_PASSWORD_HASH=' "$APP_DIR/.env" > "$tmp" || true
# Quoted for compose, which would otherwise expand the $salt / $digest parts to
# nothing (see "app configuration" in the bootstrap); bare for the native readers.
q=""
[ "$RUNTIME" = docker ] && q="'"
printf 'HOFRADAR_PASSWORD_HASH=%s%s%s\n' "$q" "$hash" "$q" >> "$tmp"
install -m 0600 -o "$SERVICE_USER" -g "$SERVICE_USER" "$tmp" "$APP_DIR/.env" && rm -f "$tmp"
rm -f /home/"$SERVICE_USER"/INITIAL_PASSWORD.txt

if [ "$RUNTIME" = native ]; then
  systemctl restart hofradar.service
else
  systemctl reload hofradar.service
fi
echo "password updated"
SETPW

cat > /usr/local/bin/hofradar-health <<'HEALTH'
#!/usr/bin/env bash
# One screen that answers "is the Pi fine?". Undervoltage and a full SD card
# are the two failure modes a Pi has that a VPS does not, so they are here.
. /usr/local/lib/hofradar-common.sh

echo "== units"
systemctl --no-pager --lines=0 status $(units) 2>/dev/null | grep -E 'Loaded:|Active:' || true
systemctl list-timers --no-pager hofradar-backup.timer 2>/dev/null | head -n 2 || true

echo
echo "== app"
code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/healthz" || true)"
echo "GET /healthz -> ${code:-no answer}   (200 = up; the body says only {\"status\":\"ok\"} to a stranger)"
echo "url:  http://$(hostname).local:${PORT}/"
for addr in $(hostname -I 2>/dev/null || true); do
  case "$addr" in *.*.*.*) echo "      http://${addr}:${PORT}/" ;; esac
done

echo
echo "== hardware"
if command -v vcgencmd >/dev/null 2>&1; then
  echo "temperature: $(vcgencmd measure_temp 2>/dev/null | cut -d= -f2)"
  throttled="$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)"
  case "$throttled" in
    0x0) echo "throttling:  none" ;;
    *)   echo "throttling:  $throttled  <- bit 0 = undervoltage NOW, bit 16 = it happened since boot."
         echo "             Undervoltage corrupts SD cards and databases. Use the official PSU." ;;
  esac
fi
echo "load:        $(cut -d' ' -f1-3 /proc/loadavg)"
free -h | awk '/^Mem:|^Swap:/ {printf "%-12s used %s of %s\n", tolower($1), $3, $2}'

echo
echo "== disk"
# One line per filesystem, not per path: on a Pi with no SSD these are all /.
df -h / "$BACKUP_DIR" ${NATIVE_DATA_DIR:+"$NATIVE_DATA_DIR"} 2>/dev/null \
  | awk 'NR==1 || !seen[$NF]++' || true

echo
echo "== backups"
latest="$(ls -1t "$BACKUP_DIR"/hofradar-*.sqlite3.gz 2>/dev/null | head -n1 || true)"
if [ -n "$latest" ]; then
  echo "newest: $latest ($(date -r "$latest" '+%Y-%m-%d %H:%M'), $(du -h "$latest" | cut -f1))"
  echo "kept:   $(ls -1 "$BACKUP_DIR"/hofradar-*.sqlite3.gz 2>/dev/null | wc -l) files, ${BACKUP_KEEP_DAYS} days"
  [ -n "${BACKUP_RSYNC_TARGET:-}" ] && echo "off-box: $BACKUP_RSYNC_TARGET"
else
  echo "none yet - the timer runs at 03:20, or run: sudo hofradar-backup"
fi
HEALTH

chmod 0755 /usr/local/bin/hofradar-backup /usr/local/bin/hofradar-update \
           /usr/local/bin/hofradar-set-password /usr/local/bin/hofradar-health
chmod 0644 /usr/local/lib/hofradar-common.sh

# ---------------------------------------------------------------------------
# systemd. One unit called hofradar.service in both runtimes, so `systemctl
# status hofradar` is the same question everywhere.
# ---------------------------------------------------------------------------
log "systemd units"
if [ "$RUNTIME" = docker ]; then
  cat > /etc/systemd/system/hofradar.service <<'UNIT'
[Unit]
Description=Hofradar (web UI + weekly scheduler)
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/hofradar/app
User=hofradar
# An ARM image build on a Pi 3 or a Pi Zero 2 is measured in tens of minutes,
# not minutes. A too-short timeout would kill the build half-way and leave you
# debugging a phantom.
TimeoutStartSec=5400
ExecStart=/usr/bin/docker compose -f docker-compose.yml -f deploy/raspberrypi/docker-compose.pi.yml up -d --build
ExecStop=/usr/bin/docker compose -f docker-compose.yml -f deploy/raspberrypi/docker-compose.pi.yml down
ExecReload=/usr/bin/docker compose -f docker-compose.yml -f deploy/raspberrypi/docker-compose.pi.yml up -d --build

[Install]
WantedBy=multi-user.target
UNIT
  rm -f /etc/systemd/system/hofradar-scheduler.service
else
  cat > /etc/systemd/system/hofradar.service <<UNIT
[Unit]
Description=Hofradar web UI
After=network-online.target
Wants=network-online.target

[Service]
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
# init-db migrates the schema before serving - the same order the container
# uses, so an older database is brought up to date on every restart.
ExecStartPre=${VENV_DIR}/bin/hofradar init-db
ExecStart=${VENV_DIR}/bin/hofradar serve --host ${BIND_ADDR} --port ${PORT}
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full
ProtectHome=yes

[Install]
WantedBy=multi-user.target
UNIT

  cat > /etc/systemd/system/hofradar-scheduler.service <<UNIT
[Unit]
Description=Hofradar weekly pipeline scheduler
After=hofradar.service network-online.target
Wants=network-online.target

[Service]
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
# A separate process on purpose: a long crawl must never block the UI, and a
# crashed crawl must never take the UI down with it (hofradar/schedule.py).
ExecStart=${VENV_DIR}/bin/python -m hofradar.schedule
Restart=on-failure
RestartSec=30
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full
ProtectHome=yes

[Install]
WantedBy=multi-user.target
UNIT
fi

cat > /etc/systemd/system/hofradar-backup.service <<'UNIT'
[Unit]
Description=Online backup of the Hofradar SQLite database
After=hofradar.service

[Service]
Type=oneshot
User=hofradar
ExecStart=/usr/local/bin/hofradar-backup
UNIT

cat > /etc/systemd/system/hofradar-backup.timer <<'UNIT'
[Unit]
Description=Nightly Hofradar backup

[Timer]
OnCalendar=*-*-* 03:20:00
RandomizedDelaySec=20m
# A Pi loses power. Persistent=true runs the missed backup after the next boot
# instead of skipping the night.
Persistent=true

[Install]
WantedBy=timers.target
UNIT

# ---------------------------------------------------------------------------
# Firewall. Note it is not reset: a Pi at home may already have rules that are
# none of this script's business.
# ---------------------------------------------------------------------------
log "firewall"
# Read the real SSH port before enabling a default-deny firewall. cat, not a
# file list on awk: a drop-in directory that matches nothing would otherwise
# make awk fail, and locking yourself out of a Pi means walking to it.
ssh_port="$(cat /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null \
  | awk '/^[[:space:]]*[Pp]ort[[:space:]]+[0-9]+/ {p=$2} END {print p}' || true)"
case "$ssh_port" in ''|*[!0-9]*) ssh_port=22 ;; esac
echo "ssh port: $ssh_port"
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw limit "${ssh_port}/tcp" comment 'ssh' >/dev/null
# mDNS, or <hostname>.local stops resolving and you are back to hunting IPs.
ufw allow 5353/udp comment 'mdns' >/dev/null
if [ "$PROXY" = caddy ]; then
  ufw allow 80/tcp comment 'http (acme + redirect)' >/dev/null
  ufw allow 443 comment 'https' >/dev/null
elif [ "$BIND_ADDR" != 127.0.0.1 ]; then
  for lan in 192.168.0.0/16 10.0.0.0/8 172.16.0.0/12; do
    ufw allow from "$lan" to any port "$PORT" proto tcp comment 'hofradar (lan)' >/dev/null
  done
fi
if [ "${HOFRADAR_TAILSCALE:-0}" = 1 ]; then
  ufw allow in on tailscale0 comment 'tailnet' >/dev/null 2>&1 || true
fi
ufw --force enable >/dev/null
ufw status verbose | sed 's/^/  /'
if [ "$RUNTIME" = docker ] && [ "$BIND_ADDR" != 127.0.0.1 ]; then
  warn "Docker publishes ports past ufw - the rule above documents intent, it
     does not enforce it. Anything that can route to this Pi can reach
     ${BIND_ADDR}:${PORT}. On a home LAN that is the point; it is also the
     reason the password gate is on."
fi
systemctl enable --now fail2ban >/dev/null 2>&1 || warn "fail2ban did not start"

# ---------------------------------------------------------------------------
# Unattended security upgrades and log caps
# ---------------------------------------------------------------------------
log "unattended upgrades"
cat > /etc/apt/apt.conf.d/51unattended-upgrades-hofradar <<'CONF'
// Raspberry Pi OS enables nothing here by default. The origins cover Debian
// security plus the two Raspberry Pi archives; a Pi that is up for a year with
// no updates is a worse risk than a reboot at 04:30.
Unattended-Upgrade::Origins-Pattern {
  "origin=Debian,codename=${distro_codename},label=Debian-Security";
  "origin=Debian,codename=${distro_codename}-security,label=Debian-Security";
  "origin=Raspbian,codename=${distro_codename},label=Raspbian";
  "origin=Raspberry Pi Foundation,codename=${distro_codename},label=Raspberry Pi Foundation";
  "origin=Ubuntu,archive=${distro_codename}-security";
};
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:30";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
CONF

# The journal on an SD card is a write amplifier with no upper bound by default.
install -d /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/hofradar.conf <<'CONF'
[Journal]
SystemMaxUse=200M
RuntimeMaxUse=64M
CONF
systemctl restart systemd-journald 2>/dev/null || true

# A wrapper so the day-to-day compose commands are short and, more to the
# point, always run from /opt/hofradar/app with both files and the right .env -
# a compose invocation that cannot read .env starts the app with an empty
# secret key and no password hash, and says nothing about it.
cat > /usr/local/bin/hofradar-compose <<'WRAP'
#!/usr/bin/env bash
. /usr/local/lib/hofradar-common.sh
[ "$RUNTIME" = docker ] || { echo "this box runs the native runtime; use journalctl -u hofradar" >&2; exit 1; }
need_root
compose "$@"
WRAP
chmod 0755 /usr/local/bin/hofradar-compose

# The CLI, run the way the service runs it: as the service user, from the
# project directory, with the same environment. Without this the native
# invocation is a `sudo -u ... env ...` incantation that people get wrong in
# the one way that matters - a missing HOFRADAR_DATA_DIR silently opens a
# second, empty database in the working directory.
cat > /usr/local/bin/hofradar-cli <<'CLI'
#!/usr/bin/env bash
# usage: sudo hofradar-cli run --sources zvg_bayern
#        sudo hofradar-cli migrate --check
#        sudo hofradar-cli config
. /usr/local/lib/hofradar-common.sh
need_root

if [ "$RUNTIME" = docker ]; then
  compose exec hofradar hofradar "$@"
  exit $?
fi

# One array element per line, so a value containing spaces survives.
env_args=()
while IFS= read -r line; do
  case "$line" in ''|'#'*) continue ;; esac
  env_args+=("$line")
done < "$APP_DIR/.env"
cd "$APP_DIR"
exec sudo -u "$SERVICE_USER" env "${env_args[@]}" "$VENV_DIR/bin/hofradar" "$@"
CLI
chmod 0755 /usr/local/bin/hofradar-cli

# ---------------------------------------------------------------------------
# Tailscale - the answer to "reach it from outside the house" that does not
# involve opening a port on the router.
# ---------------------------------------------------------------------------
TS_PENDING=0
if [ "${HOFRADAR_TAILSCALE:-0}" = 1 ]; then
  log "tailscale"
  if ! command -v tailscale >/dev/null 2>&1; then
    case "${ID:-debian}" in ubuntu) ts_distro=ubuntu ;; raspbian) ts_distro=raspbian ;; *) ts_distro=debian ;; esac
    ts_codename="${VERSION_CODENAME:-bookworm}"
    if ! curl -fsSL -o /dev/null "https://pkgs.tailscale.com/stable/${ts_distro}/${ts_codename}.noarmor.gpg"; then
      warn "Tailscale publishes nothing for ${ts_distro}/${ts_codename}; using debian/bookworm"
      ts_distro=debian; ts_codename=bookworm
    fi
    curl -fsSL "https://pkgs.tailscale.com/stable/${ts_distro}/${ts_codename}.noarmor.gpg" \
      -o /usr/share/keyrings/tailscale-archive-keyring.gpg
    curl -fsSL "https://pkgs.tailscale.com/stable/${ts_distro}/${ts_codename}.tailscale-keyring.list" \
      -o /etc/apt/sources.list.d/tailscale.list
    apt-get update
    apt-get install -y tailscale
  fi
  systemctl enable --now tailscaled
  if tailscale status >/dev/null 2>&1; then
    echo "tailscale: already logged in"
  elif [ -n "${TAILSCALE_AUTHKEY:-}" ]; then
    tailscale up --authkey "$TAILSCALE_AUTHKEY" --hostname hofradar
  else
    # `tailscale up` without a key waits for a browser, which would hang an
    # unattended bootstrap. Leave it for the operator.
    TS_PENDING=1
    echo "tailscale: installed but not logged in (no TAILSCALE_AUTHKEY given)"
  fi
fi

# ---------------------------------------------------------------------------
# DuckDNS - only relevant when this Pi is meant to answer to a public name.
# A home line's IP changes when the router reconnects, so the record has to be
# refreshed on a timer or the certificate renewal breaks at 3 a.m. in August.
# ---------------------------------------------------------------------------
case "${HOFRADAR_DOMAIN:-}" in
  *.duckdns.org)
    log "duckdns"
    if [ -n "${DUCKDNS_TOKEN:-}" ]; then
      printf 'DUCKDNS_SUBDOMAIN=%s\nDUCKDNS_TOKEN=%s\n' \
        "${HOFRADAR_DOMAIN%%.duckdns.org}" "$DUCKDNS_TOKEN" > /etc/hofradar-duckdns.env
      chmod 600 /etc/hofradar-duckdns.env
      cat > /usr/local/bin/hofradar-duckdns <<'DUCK'
#!/usr/bin/env bash
# Point the record at whatever address this line currently has. An empty ip=
# parameter tells DuckDNS to use the source address of this request, which is
# the router's public IP - exactly what a visitor would resolve.
set -euo pipefail
. /etc/hofradar-duckdns.env
answer="$(curl -fsS "https://www.duckdns.org/update?domains=${DUCKDNS_SUBDOMAIN}&token=${DUCKDNS_TOKEN}&ip=")"
[ "$answer" = "OK" ] || { echo "duckdns refused the update: ${answer}" >&2; exit 1; }
echo "duckdns: ${DUCKDNS_SUBDOMAIN}.duckdns.org updated"
DUCK
      chmod 0755 /usr/local/bin/hofradar-duckdns
      cat > /etc/systemd/system/hofradar-duckdns.service <<'UNIT'
[Unit]
Description=Refresh the DuckDNS record for this host
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/hofradar-duckdns
UNIT
      cat > /etc/systemd/system/hofradar-duckdns.timer <<'UNIT'
[Unit]
Description=Keep the DuckDNS record fresh

[Timer]
OnBootSec=30s
OnUnitActiveSec=6h
Persistent=true

[Install]
WantedBy=timers.target
UNIT
      systemctl daemon-reload
      /usr/local/bin/hofradar-duckdns || warn "the DuckDNS update failed - check the token"
      systemctl enable --now hofradar-duckdns.timer
    else
      warn "${HOFRADAR_DOMAIN} given without DUCKDNS_TOKEN - set the IP yourself at
     duckdns.org, or the certificate request will fail"
    fi
    ;;
esac

# ---------------------------------------------------------------------------
# Build, then the password, then start. In that order: hashing needs the code,
# and invariant 8 says nothing serves before the gate is real.
# ---------------------------------------------------------------------------
pi_compose() {
  ( cd "$APP_DIR" && docker compose \
      -f docker-compose.yml -f deploy/raspberrypi/docker-compose.pi.yml "$@" )
}

if [ "$RUNTIME" = docker ]; then
  log "building the image (this is the slow part - 15-25 minutes on a Pi 4, longer on a Pi 3)"
  pi_compose build
fi

if [ -n "$generated_pw" ]; then
  log "password gate"
  hash_cmd='import os; from hofradar.web.auth import hash_password; print(hash_password(os.environ["HOFRADAR_NEW_PASSWORD"]))'
  if [ "$RUNTIME" = native ]; then
    new_hash="$(HOFRADAR_NEW_PASSWORD="$generated_pw" "$VENV_DIR/bin/python" -c "$hash_cmd")"
  else
    new_hash="$(HOFRADAR_NEW_PASSWORD="$generated_pw" pi_compose run --rm --no-deps -T \
      -e HOFRADAR_NEW_PASSWORD hofradar python -c "$hash_cmd" \
      | grep -o 'pbkdf2_sha256\$[^[:space:]]*' | tail -n1)"
  fi
  [ -n "$new_hash" ] || die "could not hash a password; refusing to start an ungated UI"
  sed -i "s|^HOFRADAR_PASSWORD_HASH=.*|HOFRADAR_PASSWORD_HASH=${hash_q}${new_hash}${hash_q}|" \
    "$APP_DIR/.env"
  printf 'Hofradar login password (generated by the bootstrap):\n\n    %s\n\nChange it with: sudo hofradar-set-password\nThen delete this file.\n' \
    "$generated_pw" > "/home/${SERVICE_USER}/INITIAL_PASSWORD.txt"
  chown "$SERVICE_USER:$SERVICE_USER" "/home/${SERVICE_USER}/INITIAL_PASSWORD.txt"
  chmod 600 "/home/${SERVICE_USER}/INITIAL_PASSWORD.txt"
  echo "generated a password and left it in /home/${SERVICE_USER}/INITIAL_PASSWORD.txt"
fi

log "starting"
systemctl daemon-reload
# Not `enable --now`: that is a no-op on a unit that is already running, so a
# re-run after editing hofradar.env rewrote .env and then left the old
# containers up - bind address, proxy and password all unchanged, and a
# summary that looked finished. reload-or-restart starts a stopped unit and
# otherwise applies the change: `compose up -d` on docker, a restart on native.
systemctl enable hofradar.service
systemctl reload-or-restart hofradar.service
if [ "$RUNTIME" = native ]; then
  systemctl enable hofradar-scheduler.service
  systemctl reload-or-restart hofradar-scheduler.service
fi
systemctl enable --now hofradar-backup.timer

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo
echo "=== bootstrap done $(date -Is) ==="
if [ "$RUNTIME" = docker ]; then
  pi_compose ps
else
  systemctl --no-pager --lines=0 status hofradar.service hofradar-scheduler.service || true
fi

echo
echo "Reach it at:"
if [ "$PROXY" = caddy ] && [ -n "${HOFRADAR_DOMAIN:-}" ]; then
  echo "  https://${HOFRADAR_DOMAIN}/   (once the port forward and the certificate are in place)"
fi
if [ "$BIND_ADDR" = 127.0.0.1 ]; then
  echo "  http://127.0.0.1:${PORT}/   (loopback only - use an SSH tunnel or Tailscale)"
else
  echo "  http://$(hostname).local:${PORT}/"
  for addr in $(hostname -I 2>/dev/null || true); do
    case "$addr" in *.*.*.*) echo "  http://${addr}:${PORT}/" ;; esac
  done
fi
if [ -f "/home/${SERVICE_USER}/INITIAL_PASSWORD.txt" ]; then
  echo
  echo "Password:  sudo cat /home/${SERVICE_USER}/INITIAL_PASSWORD.txt"
fi
if [ "$TS_PENDING" = 1 ]; then
  echo
  echo "Tailscale is installed but not logged in. Finish it with:"
  echo "  sudo tailscale up --hostname hofradar"
  echo "  sudo tailscale serve --bg ${PORT}      # https://hofradar.<tailnet>.ts.net"
fi
echo
echo "Next:  sudo hofradar-health          how is the Pi doing"
echo "       sudo hofradar-backup          a backup right now"
echo "       sudo hofradar-update          pull and restart"
echo "       log: /var/log/hofradar-bootstrap.log"
