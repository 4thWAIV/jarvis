#!/usr/bin/env bash
# JARVIS installer for macOS (bash 3.2 compatible).
#
#   ./install.sh [--check] [--yes] [--no-models] [--start] [--no-open] [--dir DIR]
#
# From a checkout it checks the tools JARVIS needs, installs what is missing,
# sets the checkout up and runs JARVIS's own preflight. On a Mac with nothing
# yet, the README's one line downloads this script, which clones the
# repository (default ~/jarvis, or --dir) and carries on from inside it.
#
#   --check       only check and report; install and change nothing
#   --yes         answer yes to the one question (unattended runs)
#   --no-models   skip the speech and embedding model downloads (Settings
#                 installs them later)
#   --start       start JARVIS when done and open Chrome
#   --no-open     with --start, do not open Chrome
#   --dir DIR     where to clone when run outside a checkout
#
# Exit codes: 0 all good; 1 something required is missing or was declined;
#             2 an install step failed; 3 JARVIS did not start; 64 bad usage.
#
# Never asks for, reads or writes a key, token or password, never creates a
# .env file, and uses sudo only where Homebrew's own installer does.

REPO_URL="https://github.com/4thWAIV/jarvis.git"
CHECK=0; YES=0; MODELS=1; START=0; OPEN=1; DIR=""
FORWARD=""

usage() { sed -n '2,22p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1 ;;
    --yes|-y) YES=1 ;;
    --no-models) MODELS=0 ;;
    --start) START=1 ;;
    --no-open) OPEN=0 ;;
    --dir) shift; DIR="$1" ;;
    --dir=*) DIR="${1#--dir=}" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 64 ;;
  esac
  shift
done

# -- output --------------------------------------------------------------------

FAILED_REQUIRED=0   # a required tool or step is missing (exit 1)
FAILED_STEP=0       # an install step failed (exit 2)
SUMMARY_NOTES=""
LOG=""

say() { printf '%s\n' "$*"; }
ok() { printf '[ OK ] %s\n' "$*"; }
installed() { printf '[INSTALLED] %s\n' "$*"; }
done_() { printf '[DONE] %s\n' "$*"; }
missing() { # name, fix
  printf '[MISSING] %s\n          fix: %s\n' "$1" "$2"
  FAILED_REQUIRED=1
}
optional() { printf '[ -- ] %s\n' "$*"; }
failed() { printf '[FAILED] %s\n' "$*"; FAILED_STEP=1; }

log_line() { [ -n "$LOG" ] && printf '%s\n' "$*" >> "$LOG"; }

# run_logged LABEL CMD... : output to install.log; on failure show the step,
# the command and the last lines of its output. Returns the command's status.
run_logged() {
  local label="$1"; shift
  log_line "=== $label: $*"
  local tmp; tmp="$(mktemp -t jarvis-install)"
  "$@" > "$tmp" 2>&1
  local rc=$?
  [ -n "$LOG" ] && cat "$tmp" >> "$LOG"
  if [ $rc -ne 0 ]; then
    printf '[FAILED] %s\n  command: %s\n  last output:\n' "$label" "$*"
    tail -n 15 "$tmp" | sed 's/^/    /'
    FAILED_STEP=1
  fi
  rm -f "$tmp"
  return $rc
}

# -- versions ------------------------------------------------------------------

# version_ge A B : dotted versions, A >= B (numeric, missing parts are 0)
version_ge() {
  local a="$1" b="$2" i x y
  for i in 1 2 3; do
    x="$(printf '%s' "$a" | cut -d. -f$i)"; y="$(printf '%s' "$b" | cut -d. -f$i)"
    x="${x:-0}"; y="${y:-0}"
    [ "$x" -gt "$y" ] && return 0
    [ "$x" -lt "$y" ] && return 1
  done
  return 0
}

first_version() { printf '%s' "$1" | grep -Eo '[0-9]+(\.[0-9]+)+' | head -n 1; }

# -- bootstrap: not inside a checkout ------------------------------------------

SELF=""
if [ -n "${BASH_SOURCE:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

is_checkout() { [ -n "$1" ] && [ -f "$1/jarvis/background.py" ] && [ -f "$1/requirements.txt" ]; }

ask() { # question -> 0 for yes. Reads the terminal even when the script itself is piped in.
  [ "$YES" -eq 1 ] && return 0
  local answer=""
  if [ -t 0 ]; then read -r -p "$1 [y/N] " answer
  elif [ -r /dev/tty ]; then read -r -p "$1 [y/N] " answer < /dev/tty
  else return 1; fi
  case "$answer" in y|Y|yes|YES|Yes) return 0 ;; *) return 1 ;; esac
}

have_git() { xcode-select -p >/dev/null 2>&1 && command -v git >/dev/null 2>&1 && git --version >/dev/null 2>&1; }

if ! is_checkout "$SELF"; then
  say "JARVIS installer: not run from a checkout; cloning first."
  TARGET="${DIR:-$HOME/jarvis}"
  if ! have_git; then
    missing "Xcode Command Line Tools (git)" "run: xcode-select --install   (a window opens; when it finishes, run this again)"
    if [ "$CHECK" -eq 0 ] && ask "Start the Xcode Command Line Tools installer now?"; then
      xcode-select --install >/dev/null 2>&1
      say "Finish the installer window, then run this command again."
    fi
    exit 1
  fi
  if is_checkout "$TARGET" && [ -d "$TARGET/.git" ]; then
    ok "checkout already at $TARGET"
  elif [ -e "$TARGET" ] && [ -n "$(ls -A "$TARGET" 2>/dev/null)" ]; then
    echo "$TARGET exists and is not a JARVIS checkout. Choose another folder with --dir." >&2
    exit 1
  elif [ "$CHECK" -eq 1 ]; then
    missing "JARVIS checkout at $TARGET" "run the installer without --check to clone it"
    exit 1
  else
    git clone "$REPO_URL" "$TARGET" || { echo "git clone failed" >&2; exit 2; }
    installed "cloned $REPO_URL into $TARGET"
  fi
  ARGS=""
  [ "$CHECK" -eq 1 ] && ARGS="$ARGS --check"
  [ "$YES" -eq 1 ] && ARGS="$ARGS --yes"
  [ "$MODELS" -eq 0 ] && ARGS="$ARGS --no-models"
  [ "$START" -eq 1 ] && ARGS="$ARGS --start"
  [ "$OPEN" -eq 0 ] && ARGS="$ARGS --no-open"
  # shellcheck disable=SC2086
  exec bash "$TARGET/install.sh" $ARGS
fi

cd "$SELF" || exit 2
ROOT="$SELF"
LOG="$ROOT/install.log"
if [ "$CHECK" -eq 0 ]; then
  { printf '\n##### install.sh %s\n' "$(date '+%Y-%m-%d %H:%M:%S')"; } >> "$LOG"
else
  LOG=""   # --check changes nothing, not even the log
fi

say "JARVIS installer (macOS)"
[ "$CHECK" -eq 1 ] && say "Check only: nothing will be installed or changed."
say ""

# -- 1. the tools ----------------------------------------------------------------

TO_INSTALL=""   # newline-separated "what"; PLAN parallels it for the commands
want() { TO_INSTALL="$TO_INSTALL$1
"; }

NEED_CLT=0; NEED_BREW_PY=0; NEED_BREW_NODE=0; NEED_BREW_CHROME=0; NEED_BREW_SSL=0
NEED_CLAUDE=0; CLAUDE_OLD=0
PYTHON=""

brew_path() { command -v brew 2>/dev/null || { [ -x /opt/homebrew/bin/brew ] && echo /opt/homebrew/bin/brew; } || { [ -x /usr/local/bin/brew ] && echo /usr/local/bin/brew; }; }

if have_git; then
  ok "Xcode Command Line Tools: git $(first_version "$(git --version)")"
else
  NEED_CLT=1
  missing "Xcode Command Line Tools (git)" "xcode-select --install"
  want "Xcode Command Line Tools (opens Apple's installer window)"
  FAILED_REQUIRED=0
fi

# Python 3.12 or newer (pyproject.toml's floor is 3.11; JARVIS is qualified on 3.12).
for c in python3.12 python3.13 python3; do
  p="$(command -v "$c" 2>/dev/null)" || continue
  v="$(first_version "$("$p" --version 2>&1)")"
  if [ -n "$v" ] && version_ge "$v" 3.12.0; then PYTHON="$p"; PYVER="$v"; break; fi
done
if [ -n "$PYTHON" ]; then ok "Python $PYVER ($PYTHON)"
else NEED_BREW_PY=1; want "Python 3.12 (brew install python@3.12)"; fi

# Node.js 22 with npm
NODE_OK=0
if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
  nv="$(first_version "$(node --version 2>&1)")"
  if [ -n "$nv" ] && version_ge "$nv" 22.0.0; then
    NODE_OK=1; ok "Node.js $nv, npm $(first_version "$(npm --version 2>&1)")"
  fi
fi
[ "$NODE_OK" -eq 0 ] && { NEED_BREW_NODE=1; want "Node.js 22 with npm (brew install node@22)"; }

# Google Chrome
CHROME=""
for c in "/Applications/Google Chrome.app" "$HOME/Applications/Google Chrome.app"; do
  [ -d "$c" ] && CHROME="$c" && break
done
if [ -n "$CHROME" ]; then
  cv="$(first_version "$(defaults read "$CHROME/Contents/Info" CFBundleShortVersionString 2>/dev/null)")"
  ok "Google Chrome ${cv:+$cv }($CHROME)"
else NEED_BREW_CHROME=1; want "Google Chrome (brew install --cask google-chrome)"; fi

# openssl (the development certificate)
if command -v openssl >/dev/null 2>&1; then ok "openssl ($(openssl version 2>&1 | head -n 1))"
else NEED_BREW_SSL=1; want "openssl (brew install openssl)"; fi

# Claude Code 2.1.224 or newer
MIN_CLAUDE=2.1.224
if command -v claude >/dev/null 2>&1; then
  clv="$(first_version "$(claude --version 2>&1)")"
  if [ -n "$clv" ] && version_ge "$clv" "$MIN_CLAUDE"; then ok "Claude Code $clv"
  else NEED_CLAUDE=1; CLAUDE_OLD=1; want "Claude Code $MIN_CLAUDE or newer (found ${clv:-unknown}; claude update, else npm install -g @anthropic-ai/claude-code)"; fi
else NEED_CLAUDE=1; want "Claude Code (npm install -g @anthropic-ai/claude-code)"; fi

# Homebrew only when something must go through it
NEED_BREW=0
if [ $((NEED_BREW_PY + NEED_BREW_NODE + NEED_BREW_CHROME + NEED_BREW_SSL)) -gt 0 ]; then
  if [ -n "$(brew_path)" ]; then ok "Homebrew ($(brew_path))"
  else NEED_BREW=1; want "Homebrew (its own installer, which asks for your password)"; fi
fi

# Codex is optional
if command -v codex >/dev/null 2>&1; then ok "Codex CLI $(first_version "$(codex --version 2>&1)") (optional)"
else optional "Codex CLI not installed (optional; npm install -g @openai/codex)"; fi

# -- 2. report what is missing / install it -----------------------------------------

if [ -n "$TO_INSTALL" ]; then
  say ""
  say "Missing or too old:"
  printf '%s' "$TO_INSTALL" | sed 's/^/  - /' | sed '/^  - $/d'
  if [ "$CHECK" -eq 1 ]; then
    [ "$NEED_BREW_PY" -eq 1 ] && missing "Python 3.12 or newer" "brew install python@3.12"
    [ "$NEED_BREW_NODE" -eq 1 ] && missing "Node.js 22 with npm" "brew install node@22 && brew link --overwrite --force node@22"
    [ "$NEED_BREW_CHROME" -eq 1 ] && missing "Google Chrome" "brew install --cask google-chrome"
    [ "$NEED_BREW_SSL" -eq 1 ] && missing "openssl" "brew install openssl"
    [ "$NEED_CLAUDE" -eq 1 ] && missing "Claude Code $MIN_CLAUDE or newer" "npm install -g @anthropic-ai/claude-code"
    [ "$NEED_CLT" -eq 1 ] && FAILED_REQUIRED=1
    [ "$NEED_BREW" -eq 1 ] && missing "Homebrew" "see https://brew.sh"
  else
    say ""
    if ! ask "Install these now?"; then
      say "Nothing installed. Run again with --yes, or install them yourself."
      exit 1
    fi
    FAILED_REQUIRED=0
    say ""
    if [ "$NEED_CLT" -eq 1 ]; then
      xcode-select --install >/dev/null 2>&1
      say "Finish Apple's installer window, then run this again."
      exit 1
    fi
    if [ "$NEED_BREW" -eq 1 ]; then
      log_line "=== Homebrew installer"
      if NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" >> "$LOG" 2>&1; then
        installed "Homebrew"
      else failed "Homebrew (see $LOG)"; exit 2; fi
    fi
    BREW="$(brew_path)"
    if [ -n "$BREW" ]; then eval "$("$BREW" shellenv)"; fi
    brew_install() { # label, args...
      local label="$1"; shift
      if run_logged "$label" "$BREW" "$@"; then installed "$label"; return 0; fi; return 1
    }
    if [ "$NEED_BREW_PY" -eq 1 ]; then
      brew_install "Python 3.12" install python@3.12
    fi
    if [ "$NEED_BREW_NODE" -eq 1 ]; then
      brew_install "Node.js 22" install node@22 && run_logged "link node@22" "$BREW" link --overwrite --force node@22
      PATH="$("$BREW" --prefix node@22)/bin:$PATH"; export PATH
    fi
    [ "$NEED_BREW_CHROME" -eq 1 ] && brew_install "Google Chrome" install --cask google-chrome
    [ "$NEED_BREW_SSL" -eq 1 ] && brew_install "openssl" install openssl
    if [ "$NEED_CLAUDE" -eq 1 ]; then
      done_claude=0
      if [ "$CLAUDE_OLD" -eq 1 ] && run_logged "claude update" claude update; then done_claude=1; fi
      if [ "$done_claude" -eq 0 ]; then
        run_logged "Claude Code" npm install -g @anthropic-ai/claude-code && done_claude=1
      fi
      if [ "$done_claude" -eq 1 ]; then installed "Claude Code $(first_version "$(claude --version 2>&1)")"
      else
        say "  fix: npm install -g @anthropic-ai/claude-code   (or the native installer from claude.com/claude-code)"
        FAILED_REQUIRED=1
      fi
    fi
    # Pick the Python again now that it may exist.
    for c in python3.12 python3.13 python3; do
      p="$(command -v "$c" 2>/dev/null)" || continue
      v="$(first_version "$("$p" --version 2>&1)")"
      if [ -n "$v" ] && version_ge "$v" 3.12.0; then PYTHON="$p"; PYVER="$v"; break; fi
    done
    [ -z "$PYTHON" ] && missing "Python 3.12 or newer" "brew install python@3.12"
    command -v node >/dev/null 2>&1 && version_ge "$(first_version "$(node --version)")" 22.0.0 || missing "Node.js 22 with npm" "brew install node@22 && brew link --overwrite --force node@22"
  fi
fi

if [ "$FAILED_STEP" -eq 1 ]; then exit 2; fi
if [ "$FAILED_REQUIRED" -eq 1 ] && [ "$CHECK" -eq 0 ]; then
  say ""; say "A required tool is still missing; the checkout was not set up."; exit 1
fi

# -- 3. the checkout -----------------------------------------------------------------

say ""
say "Checkout: $ROOT"
VENV_PY="$ROOT/.venv/bin/python"
CHECKOUT_PY="$VENV_PY"
READY=1   # whether the venv is usable for the Python-side checks

if [ -x "$VENV_PY" ]; then
  vv="$(first_version "$("$VENV_PY" --version 2>&1)")"
  if [ -n "$vv" ] && version_ge "$vv" 3.12.0; then ok ".venv (Python $vv)"
  else
    missing ".venv uses Python ${vv:-unknown}, older than 3.12" "remove the folder .venv and run this again"
    READY=0
  fi
elif [ "$CHECK" -eq 1 ] || [ -z "$PYTHON" ]; then
  missing ".venv" "run ./install.sh (python3.12 -m venv .venv)"
  READY=0
else
  if run_logged "create .venv" "$PYTHON" -m venv "$ROOT/.venv"; then done_ ".venv created with Python $PYVER"
  else READY=0; fi
fi

# step LABEL DONE-CMD DO-CMD... : skipped when DONE-CMD succeeds
# (kept simple: callers write the two halves)
if [ "$READY" -eq 1 ]; then
  check_py() { "$CHECKOUT_PY" -m jarvis.install_check "$@"; }

  # Python dependencies
  if check_py stamp-ok pip >/dev/null 2>&1; then ok "Python dependencies already installed"
  elif [ "$CHECK" -eq 1 ]; then missing "Python dependencies" "$CHECKOUT_PY -m pip install -r requirements.txt"
  else
    if run_logged "pip install -r requirements.txt" "$CHECKOUT_PY" -m pip install -r requirements.txt; then
      check_py stamp-set pip; done_ "Python dependencies installed"
    fi
  fi

  # Frontend dependencies
  if check_py stamp-ok npm >/dev/null 2>&1; then ok "Frontend dependencies already installed"
  elif [ "$CHECK" -eq 1 ]; then missing "Frontend dependencies" "npm ci --prefix frontend"
  else
    if run_logged "npm ci --prefix frontend" npm ci --prefix frontend; then
      check_py stamp-set npm; done_ "Frontend dependencies installed"
    fi
  fi

  # Development certificate (the Vite proxy speaks HTTPS to the backend)
  if check_py done certs >/dev/null 2>&1; then ok "Development certificate (cert.pem, key.pem)"
  elif [ "$CHECK" -eq 1 ]; then missing "Development certificate" "$CHECKOUT_PY -m jarvis.background certs"
  else
    if run_logged "development certificate" "$CHECKOUT_PY" -m jarvis.background certs; then done_ "Development certificate made"; fi
  fi

  # Playwright's Chromium
  if check_py done playwright >/dev/null 2>&1; then ok "Playwright Chromium"
  elif [ "$CHECK" -eq 1 ]; then missing "Playwright Chromium" "$CHECKOUT_PY -m playwright install chromium"
  else
    if run_logged "playwright install chromium" "$CHECKOUT_PY" -m playwright install chromium; then done_ "Playwright Chromium installed"; fi
  fi

  # Models: large, optional
  if [ "$MODELS" -eq 0 ]; then
    optional "Speech and embedding models skipped (--no-models); Settings installs them later"
  else
    sizes="$(check_py models 2>/dev/null | tr '\n' ';' | sed 's/;$//; s/;/; /g')"
    if check_py done speech >/dev/null 2>&1; then ok "English speech model"
    elif [ "$CHECK" -eq 1 ]; then optional "English speech model not installed (optional: $CHECKOUT_PY scripts/setup_speech_input.py)"
    else
      say "Downloading: ${sizes:-the speech and embedding models}"
      if run_logged "speech model" "$CHECKOUT_PY" scripts/setup_speech_input.py; then done_ "English speech model installed"; fi
    fi
    if check_py done embedding >/dev/null 2>&1; then ok "Embedding model"
    elif [ "$CHECK" -eq 1 ]; then optional "Embedding model not installed (optional: $CHECKOUT_PY scripts/setup_embedding_model.py)"
    else
      if run_logged "embedding model" "$CHECKOUT_PY" scripts/setup_embedding_model.py; then done_ "Embedding model installed"; fi
    fi
  fi

  # -- 4. preflight -----------------------------------------------------------------
  say ""
  say "JARVIS preflight (warnings are things to do, not failures):"
  if out="$(check_py preflight 2>&1)"; then
    printf '%s\n' "$out" | sed 's/^/  /'
    log_line "=== preflight"; log_line "$out"
  else
    printf '[FAILED] preflight\n%s\n' "$(printf '%s' "$out" | tail -n 15 | sed 's/^/    /')"
    log_line "=== preflight failed"; log_line "$out"
    FAILED_STEP=1
  fi

  # The talk-to-jarvis skill. It needs a Claude Code or Codex home, which a first `claude`
  # run makes (preflight's login check is one), so it comes after preflight.
  check_py done skill >/dev/null 2>&1; skill=$?
  if [ "$skill" -eq 0 ]; then ok "talk-to-jarvis skill"
  elif [ "$skill" -eq 2 ]; then optional "talk-to-jarvis skill: no Claude Code or Codex home yet; run 'claude' once, then run this again"
  elif [ "$CHECK" -eq 1 ]; then optional "talk-to-jarvis skill not installed (optional: $CHECKOUT_PY -m jarvis.session_skill install)"
  else
    if run_logged "session skill" "$CHECKOUT_PY" -m jarvis.session_skill install; then done_ "talk-to-jarvis skill installed"; fi
  fi
fi

# -- 5. what only the person can do -----------------------------------------------------

say ""
say "Left for you (this script never handles these):"
say "  1. Sign in to Claude Code: run 'claude', then '/login' with your Claude subscription."
say "     JARVIS never uses an API key."
say "  2. Fish Audio key: open Settings, Voice in JARVIS and enter it there (from fish.audio)."
say "  3. Accessibility: System Settings, Privacy & Security, Accessibility, tick your terminal app."
say "  4. Chrome: open http://localhost:5173 and allow the microphone for that address."

# -- 6. start ----------------------------------------------------------------------------

STARTED=0
if [ "$CHECK" -eq 0 ] && [ "$FAILED_STEP" -eq 0 ] && [ "$FAILED_REQUIRED" -eq 0 ]; then
  if [ "$START" -eq 0 ] && { [ -t 0 ] || [ -r /dev/tty ]; } && [ "$YES" -eq 0 ]; then
    ask "Start JARVIS now?" && START=1
  fi
  if [ "$START" -eq 1 ]; then
    say ""
    if "$CHECKOUT_PY" -m jarvis.background status >/dev/null 2>&1; then
      ok "JARVIS is already running"; STARTED=1
    else
      log_line "=== jarvis.background start"
      if "$CHECKOUT_PY" -m jarvis.background start 2>&1 | tee -a "$LOG"; then STARTED=1
      else
        printf '[FAILED] start\n  command: %s -m jarvis.background logs backend\n' "$CHECKOUT_PY"
        "$CHECKOUT_PY" -m jarvis.background logs backend -n 15 2>&1 | sed 's/^/    /'
        exit 3
      fi
    fi
    [ "$STARTED" -eq 1 ] && [ "$OPEN" -eq 1 ] && open -a "Google Chrome" "http://localhost:5173" 2>/dev/null
    say "  status: $CHECKOUT_PY -m jarvis.background status"
    say "  stop:   $CHECKOUT_PY -m jarvis.background stop"
    say "  logs:   $CHECKOUT_PY -m jarvis.background logs backend   (or vite)"
  fi
fi

# -- summary -----------------------------------------------------------------------------

say ""
if [ "$FAILED_STEP" -eq 1 ]; then
  say "Summary: a step failed. Details are in $ROOT/install.log."
  exit 2
elif [ "$FAILED_REQUIRED" -eq 1 ]; then
  say "Summary: something required is missing (see MISSING above)."
  exit 1
fi
if [ "$CHECK" -eq 1 ]; then say "Summary: everything required is in place."
else say "Summary: JARVIS is installed. Open http://localhost:5173 in Chrome once it is running."; fi
exit 0
