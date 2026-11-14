#!/usr/bin/env bash
# MEIKO AGENT on Termux (Android). 0.0.0.0 is not used: Termux is not public.
#
#   curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/<branch>/scripts/termux_setup.sh | bash
#
# or clone first, then:  bash scripts/termux_setup.sh
#
# What this does, and why it is a script rather than a sentence in the README:
# Termux has no system image with the wheels this project needs. asyncpg,
# cryptography, Pillow and greenlet are compiled from source there, which fails
# unless clang, rust and the headers are installed *first*; redis-server can be
# installed but is not running by default; and Python's default storage is inside
# the app sandbox, which Android may clear when the app is killed.
set -euo pipefail

log() { printf '\033[32m[termux]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[termux]\033[0m %s\n' "$*"; }

if [ -z "${TERMUX_VERSION:-}" ] && [ ! -d /data/data/com.termux ]; then
  warn "This does not look like Termux. Continuing anyway (the steps are harmless)."
fi

log "1/6 system packages"
pkg update -y >/dev/null
pkg install -y python clang rust binutils libffi openssl openssl-tools \
  libsqlite postgresql redis git curl ffmpeg make pkg-config

log "2/6 python: $(python --version 2>&1)"
# Termux' python is 3.12+; the project needs 3.11+.
python - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit(f"python {sys.version.split()[0]} is too old; pkg install python")
PY

log "3/6 python dependencies (this compiles asyncpg, cryptography, Pillow, greenlet)"
python -m pip install --upgrade pip wheel setuptools >/dev/null
# Build from source deliberately: there are no Android wheels, and a silent fallback
# to a prebuilt linux wheel produces an ImportError at runtime instead of a clear
# build failure here.
python -m pip install -r requirements.txt

log "4/6 data directory outside the app sandbox"
# Termux' HOME lives under the app's private data; a bot's SQLite database and
# encryption key should survive a 'force stop'.  Put them in shared storage when
# available, otherwise in a stable directory inside HOME.
if [ -d "$HOME/storage/shared" ]; then
  DATA_DIR="$HOME/storage/shared/meiko-data"
else
  DATA_DIR="$HOME/meiko-data"
  warn "shared storage not set up (run: termux-setup-storage). Using $DATA_DIR"
fi
mkdir -p "$DATA_DIR"
log "    DATA_DIR=$DATA_DIR"

log "5/6 local services: redis (the API needs it only for multi-process, but the"
log "    scheduler/worker heartbeats are happier with it)"
if ! command -v redis-server >/dev/null; then
  warn "redis-server missing; the app will use a per-process cache (fine for one process)"
else
  (redis-cli ping 2>/dev/null | grep -q PONG) || {
    redis-server --daemonize yes --save '' --appendonly no
    sleep 1
    redis-cli ping >/dev/null && log "    redis started"
  }
fi

log "6/6 configuration"
ENV_FILE="$HOME/meiko.env"
if [ ! -f "$ENV_FILE" ]; then
  python - <<'PY' > "$ENV_FILE"
import os, secrets
from cryptography.fernet import Fernet
data_dir = os.environ.get("MEIKO_DATA_DIR", os.path.expanduser("~/meiko-data"))
print("ENVIRONMENT=development            # single process, no TLS: do not expose it")
print("AUTH_DEV_MODE=true                 # the console login; Android is a personal device")
print(f"DATA_DIR={data_dir}")
print("DATABASE_URL=                      # empty: SQLite inside DATA_DIR")
print("REDIS_URL=redis://127.0.0.1:6379/0")
print(f"SECRET_KEY={secrets.token_urlsafe(48)}")
print(f"ENCRYPTION_KEY={Fernet.generate_key().decode()}")
print("BOT_TOKEN=                         # optional: a bot started from the DB instead")
PY
  chmod 600 "$ENV_FILE"
  log "    wrote $ENV_FILE (chmod 600)"
fi

# The database can live in the cloud while the bot runs on the phone. Point
# DATABASE_URL at a managed Postgres and the phone holds no state that a restart can
# lose; leave it empty and everything stays in DATA_DIR as SQLite.
if [ -z "$(grep -E '^DATABASE_URL=.+' "$ENV_FILE" 2>/dev/null)" ]; then
  if [ -t 0 ]; then
    printf '\nPaste a Postgres URL to keep data in the cloud (Supabase etc.),\n'
    printf 'or press Enter to stay on SQLite: '
    read -r PG_URL
    if [ -n "${PG_URL:-}" ]; then
      case "$PG_URL" in
        postgres://*|postgresql://*) ;;
        *) warn "that is not a postgres:// URL; leaving SQLite in place"; PG_URL="" ;;
      esac
    fi
    if [ -n "${PG_URL:-}" ]; then
      # Rewrite in place without ever echoing the password back to the screen.
      python - "$ENV_FILE" "$PG_URL" <<'PY_SET'
import sys
path, url = sys.argv[1], sys.argv[2]
lines = [l for l in open(path).read().splitlines() if not l.startswith("DATABASE_URL=")]
note = "# SQLite is the fallback when this is unreachable; a bad URL must not strand the phone"
lines.insert(3, f"DATABASE_URL={url}")
lines.insert(4, note)
open(path, "w").write("\n".join(lines) + "\n")
PY_SET
      chmod 600 "$ENV_FILE"
      log "    DATABASE_URL set (cloud Postgres)"
    fi
  else
    log "    DATABASE_URL empty: SQLite in DATA_DIR (non-interactive run)"
  fi
fi

# A cloud URL that the phone cannot reach is the one failure mode that looks like a
# broken app: check it now, while the operator is still looking at the screen.
DB_URL="$(grep -E '^DATABASE_URL=.+' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)"
if [ -n "${DB_URL:-}" ]; then
  log "    checking the database URL"
  if ! python - "$DB_URL" <<'PY_CHECK'
import asyncio, sys
from urllib.parse import urlsplit

from sqlalchemy import text

from meiko.config import Settings
from meiko.db.base import create_engine

url = sys.argv[1]
parts = urlsplit(url)
where = f"{parts.hostname}:{parts.port or 5432}"

async def main() -> bool:
    settings = Settings(database_url=url)
    engine = create_engine(settings, settings.resolved_database_url)
    try:
        async with engine.connect() as conn:
            await asyncio.wait_for(conn.execute(text("select 1")), timeout=20)
        print(f"    reachable: {where}")
        return True
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        print(f"    NOT reachable: {where}")
        print(f"      {type(exc).__name__}: {str(exc).splitlines()[0][:160]}")
        if (parts.hostname or "").startswith("db.") and "supabase" in (parts.hostname or ""):
            print("      That is Supabase's direct host: it is IPv6-only on the free")
            print("      tier. Use the session pooler instead -- the same dashboard")
            print("      page, connection method 'Session pooler', port 5432.")
        else:
            print("      Check the password, the project (a paused project has no")
            print("      endpoint), and that the phone has a working connection.")
        return False
    finally:
        await engine.dispose()

sys.exit(0 if asyncio.run(main()) else 1)
PY_CHECK
  then
    warn "the database URL is in $ENV_FILE and does not work from this phone"
    warn "fix it, or remove that line to fall back to SQLite in DATA_DIR"
  fi
fi

cat <<EOF

Next steps
----------
  export \$(grep -v '^#' "$ENV_FILE" | xargs)      # load the settings
  python start.py --service migrate                # create the database
  python start.py                                  # API + console on :8000

  # open the console from the phone: http://127.0.0.1:8000/docs
  # keep it alive while Termux is in the background:
  termux-wake-lock
  # after a reboot, or when Android kills the session:
  bash scripts/termux_boot.sh

Notes
-----
  * Do NOT publish this: AUTH_DEV_MODE=true and no TLS. It is a personal control
    plane for your own phone.
  * The web console (webapp/) needs Node and a production build; on a phone it is
    usually easier to keep the API here and host the console elsewhere, or just use
    /docs.
  * ffmpeg from pkg gives real encodes; imageio-ffmpeg is only a fallback.
EOF
