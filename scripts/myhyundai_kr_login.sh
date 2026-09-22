#!/usr/bin/env bash
# Create and verify renewable MyHyundai Korea credentials through an
# interactive Pleos browser login.

set -euo pipefail

if [[ -t 1 ]] && command -v tput >/dev/null 2>&1 && [[ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]]; then
  BOLD=$(tput bold); DIM=$(tput dim); RESET=$(tput sgr0)
  BLUE=$(tput setaf 4); GREEN=$(tput setaf 2); YELLOW=$(tput setaf 3); RED=$(tput setaf 1)
else
  BOLD=""; DIM=""; RESET=""; BLUE=""; GREEN=""; YELLOW=""; RED=""
fi

TOTAL_STAGES=0
TOTAL_MINUTES=0

_STAGE_INDEX=0
_MINUTES_ELAPSED=0

_clear() {
  [[ -t 1 ]] || return 0
  if command -v tput >/dev/null 2>&1; then tput clear; else printf '\033[2J\033[3J\033[H'; fi
}

banner() {
  _clear
  printf '\n%s%s  %s%s\n' "$BOLD" "$BLUE" "$1" "$RESET"
  printf '%s  %s stages · about %s minutes%s\n\n' \
    "$DIM" "$TOTAL_STAGES" "$TOTAL_MINUTES" "$RESET"
  printf '%s  You drive the browser; this wizard tells you exactly what to do and\n' "$DIM"
  printf '  captures the values you copy back. Stop any time with Ctrl-C and re-run\n'
  printf '  later — it remembers values already saved.%s\n' "$RESET"
  pause "Ready to start?"
}

stage() {
  _clear
  _STAGE_INDEX=$((_STAGE_INDEX + 1))
  local remaining=$((TOTAL_MINUTES - _MINUTES_ELAPSED))
  (( remaining < 0 )) && remaining=0
  _MINUTES_ELAPSED=$((_MINUTES_ELAPSED + ${2:-0}))
  printf '\n%s%s▸ Stage %s/%s · %s%s  %s(~%s min left)%s\n' \
    "$BOLD" "$BLUE" "$_STAGE_INDEX" "$TOTAL_STAGES" "$1" "$RESET" "$DIM" "$remaining" "$RESET"
}

say()  { printf '  %s\n' "$1"; }
step() { printf '  %s•%s %s\n' "$BLUE" "$RESET" "$1"; }
note() { printf '  %s%s%s\n' "$DIM" "$1" "$RESET"; }
warn() { printf '  %s⚠ %s%s\n' "$YELLOW" "$1" "$RESET"; }

open_url() {
  local url="$1"
  printf '  %s↗ opening%s %s\n' "$GREEN" "$RESET" "$url"
  { if   command -v wslview     >/dev/null 2>&1; then wslview "$url"
    elif command -v explorer.exe >/dev/null 2>&1; then explorer.exe "$url"
    elif command -v xdg-open    >/dev/null 2>&1; then xdg-open "$url"
    elif command -v open        >/dev/null 2>&1; then open "$url"
    else warn "couldn't open a browser — visit it manually: $url"; fi
  } >/dev/null 2>&1 || warn "couldn't open a browser — visit it manually: $url"
}

pause() {
  printf '  %s%s%s ' "$DIM" "${1:-Press Enter to continue}" "$RESET"
  read -r _ || true
}

confirm() {
  local reply=""
  printf '  %s? %s [y/N] ' "$YELLOW" "$1"
  read -r reply || true
  [[ "$reply" =~ ^[Yy] ]]
}

ask_secret() {
  local key="$1" prompt="$2" input
  printf '  %s%s%s ' "$BOLD" "$prompt" "$RESET"
  read -rs input || true
  printf '\n'
  printf -v "$key" '%s' "$input"
}

finish() {
  _clear
  printf '\n%s%s  ✓ Setup complete%s\n' "$BOLD" "$GREEN" "$RESET"
  printf '\n'
}

TOTAL_STAGES=3
TOTAL_MINUTES=4

banner "MyHyundai Korea renewable session setup"

stage "Preflight" 1
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)
SESSION_FILE="${MYHYUNDAI_KR_SESSION_FILE:-${XDG_STATE_HOME:-$HOME/.local/state}/hyundai-kia-connect-api/myhyundai-kr.json}"
if [[ -x "$REPO_DIR/.venv/bin/python" ]]; then
  SESSION_PYTHON="$REPO_DIR/.venv/bin/python"
else
  SESSION_PYTHON="${PYTHON:-python3}"
fi
if ! "$SESSION_PYTHON" -c 'import Crypto, requests' >/dev/null 2>&1; then
  warn "Python dependencies are missing. Install this package before continuing."
  exit 1
fi
say "The browser authorization code is exchanged once for renewable credentials."
note "Session file: $SESSION_FILE"
note "The file contains bearer credentials. It never contains your password or PIN."

stage "Pleos browser sign-in" 2
MYHYUNDAI_REDIRECT_URL=""
if [[ -f "$SESSION_FILE" ]] && ! confirm "Replace the existing renewable session?"; then
  say "Keeping the existing session."
else
  AUTHORIZATION_URL=$(PYTHONPATH="$REPO_DIR" "$SESSION_PYTHON" - <<'PY'
from hyundai_kia_connect_api import VehicleManager

manager = VehicleManager(10, 2, "", "", "", language="ko")
print(manager.get_authorization_url())
PY
)
  step "Open browser developer tools, select Network, and enable Preserve log."
  open_url "$AUTHORIZATION_URL"
  step "On the Hyundai login form, turn on the Pleos account login switch at the bottom."
  step "Keep developer tools open, then sign in with your Pleos credentials."
  step "After the browser reaches hyundai.com, filter Network for oneapp.hyundai.com/redirect."
  step "Open that document request and copy its complete Request URL."
  note "The callback page forwards immediately in a normal browser. Preserve log keeps the one-time code available."
  ask_secret MYHYUNDAI_REDIRECT_URL "Paste the callback Request URL:"
  if [[ -z "$MYHYUNDAI_REDIRECT_URL" ]]; then
    warn "No redirect address was provided. Nothing was saved."
    exit 1
  fi
fi

stage "Refresh and read-only check" 1
MYHYUNDAI_REDIRECT_URL="$MYHYUNDAI_REDIRECT_URL" \
MYHYUNDAI_KR_SESSION_FILE="$SESSION_FILE" \
PYTHONPATH="$REPO_DIR" "$SESSION_PYTHON" - <<'PY'
import json
import os
import tempfile
from pathlib import Path

from hyundai_kia_connect_api import Token, VehicleManager

redirect_url = os.environ.pop("MYHYUNDAI_REDIRECT_URL")
session_file = Path(os.environ["MYHYUNDAI_KR_SESSION_FILE"]).expanduser()


def save_session(token: Token) -> None:
    session_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{session_file.name}.",
        suffix=".tmp",
        dir=session_file.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(token.to_persistent_dict(), file)
            file.write("\n")
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, session_file)
        session_file.chmod(0o600)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


if redirect_url:
    manager = VehicleManager(10, 2, "", "", "", language="ko")
    manager.login_with_redirect_url(redirect_url)
else:
    token_data = json.loads(session_file.read_text(encoding="utf-8"))
    manager = VehicleManager(
        10,
        2,
        "",
        "",
        "",
        token=Token.from_dict(token_data),
        language="ko",
    )

manager.check_and_refresh_token()
save_session(manager.token)
manager.update_all_vehicles_with_cached_state()
print(f"  Session works. Found {len(manager.vehicles)} vehicle(s).")
print(f"  Access token expires at {manager.token.valid_until.isoformat()}.")
print("  The saved refresh credentials will obtain another access token when needed.")
PY
unset MYHYUNDAI_REDIRECT_URL
chmod 600 "$SESSION_FILE"
note "Keep $SESSION_FILE private and out of source control."

finish
