#!/usr/bin/env bash
# Start MEIKO AGENT on Termux, idempotently.
#
# Used after a reboot, or when Android killed the Termux session (which it does when
# it wants memory — this is a phone, not a server). Safe to run repeatedly:
#   bash scripts/termux_boot.sh
#
# Wire it into Termux:Boot (install the Termux:Boot app, then put this in
# ~/.termux/boot/) so the bot comes back after a restart without you unlocking
# anything:
#
#   mkdir -p ~/.termux/boot
#   cat > ~/.termux/boot/meiko.sh <<'EOS'
#   #!/data/data/com.termux/files/usr/bin/sh
#   termux-wake-lock
#   cd ~/Meikoagent && sh scripts/termux_boot.sh
#   EOS
#   chmod +x ~/.termux/boot/meiko.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${MEIKO_ENV_FILE:-$HOME/meiko.env}"
LOG_FILE="${MEIKO_LOG_FILE:-$HOME/meiko.log}"
PID_FILE="$HOME/meiko.pid"

log() { printf '\033[32m[boot]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[boot]\033[0m %s\n' "$*"; }

command -v termux-wake-lock >/dev/null && termux-wake-lock && log "wake lock held"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  log "already running (pid $(cat "$PID_FILE"))"
  exit 0
fi

# Redis first: the app refuses to run with a shared-state requirement if it is
# configured but unreachable.
if command -v redis-server >/dev/null; then
  if ! redis-cli ping 2>/dev/null | grep -q PONG; then
    redis-server --daemonize yes --save '' --appendonly no
    sleep 1
    redis-cli ping >/dev/null && log "redis started"
  fi
fi

# An unreachable redis in the environment would stop the API; on a phone one process
# is the norm, so fall back to the in-process cache instead of failing.
export REQUIRE_REDIS=0

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
  log "loaded $ENV_FILE"
else
  warn "no $ENV_FILE; run scripts/termux_setup.sh first"
fi

cd "$REPO_DIR"
log "migrating"
python start.py --service migrate >>"$LOG_FILE" 2>&1 || {
  warn "migration failed; see $LOG_FILE"
  exit 1
}

log "starting API + console (log: $LOG_FILE)"
nohup python start.py >>"$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
sleep 3

if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  log "running (pid $(cat "$PID_FILE")) — console at http://127.0.0.1:8000/docs"
else
  warn "it exited immediately; last lines of $LOG_FILE:"
  tail -20 "$LOG_FILE"
  exit 1
fi
