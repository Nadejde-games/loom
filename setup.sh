#!/usr/bin/env bash
#
# Loom setup wizard — one command to go from a fresh clone to a running world.
#
# Works on macOS and Ubuntu/Debian. Interactive: it asks whether you want the
# authoring workbench, and whether to run on OpenRouter (hosted, needs a key) or
# Ollama (local, no key). It creates a virtualenv, installs the package, sets up
# your chosen backend, and writes a .env you can run against.
#
#   ./setup.sh
#
# It never overwrites an existing .env value you did not change. Re-run it any
# time — it is idempotent.
#
# Testing / preview: LOOM_SETUP_DRY_RUN=1 prints the install + model-pull steps
# instead of executing them (LOOM_SETUP_ENV_FILE redirects the .env it writes).

set -euo pipefail

# ── Locations ───────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
VENV_DIR="$REPO_ROOT/.venv"
ENV_FILE="${LOOM_SETUP_ENV_FILE:-$REPO_ROOT/.env}"
ENV_EXAMPLE="$REPO_ROOT/.env.example"
DRY_RUN="${LOOM_SETUP_DRY_RUN:-0}"

# ── Pretty output (only when attached to a terminal) ────────────────────────
if [ -t 1 ]; then
    BOLD=$'\033[1m'; DIM=$'\033[2m'; RESET=$'\033[0m'
    GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BLUE=$'\033[34m'; RED=$'\033[31m'
else
    BOLD=''; DIM=''; RESET=''; GREEN=''; YELLOW=''; BLUE=''; RED=''
fi

say()  { printf '%b\n' "$*"; }
step() { printf '\n%b==>%b %b%s%b\n' "$BLUE" "$RESET" "$BOLD" "$*" "$RESET"; }
ok()   { printf '  %b✓%b %s\n' "$GREEN" "$RESET" "$*"; }
warn() { printf '  %b⚠%b %s\n' "$YELLOW" "$RESET" "$*"; }
die()  { printf '\n%bError:%b %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }

# run CMD... — execute, or just print it in dry-run mode.
run() {
    if [ "$DRY_RUN" = "1" ]; then
        printf '  %b[dry-run]%b %s\n' "$DIM" "$RESET" "$*"
    else
        "$@"
    fi
}

# ask_yn "Question?" default(y|n) — return 0 for yes, 1 for no.
ask_yn() {
    local prompt="$1" default="${2:-y}" reply hint
    if [ "$default" = "y" ]; then hint="[Y/n]"; else hint="[y/N]"; fi
    read -r -p "$(printf '%b?%b %s %s ' "$BLUE" "$RESET" "$prompt" "$hint")" reply || reply=''
    reply="${reply:-$default}"
    case "$reply" in [Yy]*) return 0;; *) return 1;; esac
}

# ask_val "Prompt" "default" — echo the answer (default if blank).
ask_val() {
    local prompt="$1" default="${2:-}" reply
    read -r -p "$(printf '%b?%b %s [%s] ' "$BLUE" "$RESET" "$prompt" "$default")" reply || reply=''
    printf '%s' "${reply:-$default}"
}

# set_env_var KEY VALUE — write into $ENV_FILE, replacing the line in place if
# the key is already set (uncommented), else appending. Portable (no `sed -i`,
# whose flags differ between GNU and BSD). Preserves every other line untouched.
set_env_var() {
    local key="$1" val="$2" tmp
    [ -f "$ENV_FILE" ] || : > "$ENV_FILE"
    if grep -qE "^${key}=" "$ENV_FILE"; then
        tmp="$(mktemp)"
        awk -v k="$key" -v v="$val" '
            $0 ~ "^" k "=" { print k "=" v; next }
            { print }
        ' "$ENV_FILE" > "$tmp"
        mv "$tmp" "$ENV_FILE"
    else
        printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
    fi
    ok "set $key in ${ENV_FILE/#$REPO_ROOT\//}"
}

# ── Ollama model menu ───────────────────────────────────────────────────────
# The models offered for the director + authoring-agent tier. EDIT THIS TABLE freely to add
# sizes that suit your GPU — one entry per line as "tag|approx VRAM|note". A wrong tag no
# longer aborts the wizard (it just re-prompts), and a "custom" entry is always appended so
# you can type any tag at runtime. VRAM figures are rough q4 estimates.
OLLAMA_DIRECTOR_MODELS=(
    "qwen3.6:27b|~17 GB|dense — strong judgment; best for authoring"
    "qwen3.5:35b-a3b|~24 GB|MoE — fastest, heaviest; can be more hit-and-miss for authoring"
    "qwen3.5:9b|~6 GB|dense — a lighter option for a smaller GPU"
    "qwen3.5:4b|~3 GB|dense — the smallest, for a modest GPU / laptop"
)
# macOS only: Apple-Silicon Metal (MLX) builds — the preferred models on a Mac, so they are
# prepended to the menu on Darwin (shown first). VRAM ~= unified memory used.
OLLAMA_DIRECTOR_MODELS_MACOS=(
    "qwen3.5:35b-mlx|~20 GB|MLX — fastest on Metal if you have the RAM, but only 3b active params so may be less reliable for authoring"
    "qwen3.5:27b-mlx|~16 GB|MLX — will feel very slow during gameplay on Metal; OK for authoring"
    "qwen3.5:9b-mlx|~6 GB|MLX — a lighter middle option on Metal"
    "qwen3.5:4b-mlx|~3 GB|MLX — good for quick-and-dirty gameplay on Metal; probably not OK for authoring"
)
# Hosted OpenRouter slugs for the three roles — the same sizes as the portable local menu,
# as OpenRouter model IDs (no Metal builds; those are Mac-local only). NOT pulled: an invalid
# slug surfaces at first inference, not at setup. Edit freely; a "custom" entry is appended.
OPENROUTER_MODELS=(
    "qwen/qwen3.6-27b|dense|strong judgment; best for authoring"
    "qwen/qwen3.6-35b-a3b|MoE|fastest; the default NPC tier"
    "qwen/qwen3.5-9b|dense|a lighter, cheaper option"
)

# select_model — print MODEL_MENU (entries "tag|col|note"), read a choice, set REPLY_MODEL.
# MODEL_KIND names the thing typed for a custom pick; MODEL_CUSTOM_DEFAULT seeds it.
# MODEL_DEFAULT (a tag) marks the per-role suggested option and pre-selects its number.
select_model() {
    local i=1 entry tag col note choice default_idx=1 mark
    for entry in "${MODEL_MENU[@]}"; do
        IFS='|' read -r tag col note <<< "$entry"
        mark=""
        if [ "$tag" = "${MODEL_DEFAULT:-}" ]; then
            default_idx=$i
            mark=" ${DIM}(suggested)${RESET}"
        fi
        printf '    %b%d)%b %-21s %b%-5s%b %s%s\n' "$BOLD" "$i" "$RESET" "$tag" "$DIM" "$col" "$RESET" "$note" "$mark"
        i=$((i + 1))
    done
    printf '    %b%d)%b %-21s %b%-5s%b %s\n' "$BOLD" "$i" "$RESET" "custom" "$DIM" "" "$RESET" "type your own ${MODEL_KIND:-tag}"
    choice="$(ask_val 'Model number' "$default_idx")"
    if ! printf '%s' "$choice" | grep -qE '^[0-9]+$' || [ "$choice" -lt 1 ] || [ "$choice" -gt "$i" ]; then
        warn "not a valid choice — using the suggested option $default_idx"
        choice=$default_idx
    fi
    if [ "$choice" -eq "$i" ]; then
        REPLY_MODEL="$(ask_val "${MODEL_KIND:-tag}" "${MODEL_CUSTOM_DEFAULT:-}")"
    else
        entry="${MODEL_MENU[$((choice - 1))]}"
        REPLY_MODEL="${entry%%|*}"
    fi
}

# select_and_pull_model — menu-pick a model and pull it, re-prompting on a bad tag; sets
# REPLY_MODEL. Deduplicates: a tag already pulled this run (a role reusing another's model)
# is not fetched again. PULLED_MODELS is the space-delimited set of tags pulled so far.
PULLED_MODELS=" "
select_and_pull_model() {
    while :; do
        select_model
        case "$PULLED_MODELS" in
            *" $REPLY_MODEL "*) break ;;              # already pulled this run
        esac
        if pull_model "$REPLY_MODEL"; then
            PULLED_MODELS="$PULLED_MODELS$REPLY_MODEL "
            break
        fi
        say "  pick another model, or enter a tag that exists in your library."
    done
}

# ── Port helpers ────────────────────────────────────────────────────────────
# port_free PORT — 0 if a TCP listener can bind 127.0.0.1:PORT (free), else 1.
# Uses the interpreter (stdlib only) so it works the same on macOS and Linux.
port_free() {
    "$VENV_PY" - "$1" <<'PY' 2>/dev/null
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
finally:
    s.close()
sys.exit(0)
PY
}

# next_free_port START — echo the first free port at/after START (scans up to +50).
next_free_port() {
    local p="$1" limit=$(( $1 + 50 ))
    while [ "$p" -le "$limit" ]; do
        if port_free "$p"; then printf '%s' "$p"; return 0; fi
        p=$((p + 1))
    done
    printf '%s' "$1"
}

# pull_model TAG — pull TAG, returning non-zero (WITHOUT aborting the wizard) when it does
# not exist in the Ollama library, so the caller can re-prompt instead of the whole run dying.
pull_model() {
    if run ollama pull "$1"; then
        return 0
    fi
    warn "could not pull '$1' — that tag may not exist in the Ollama library."
    say "  Browse valid tags at https://ollama.com/library (e.g. .../library/qwen3)."
    return 1
}

# warm_model MODEL — preload MODEL into Ollama's memory so the first real inference doesn't
# pay the weight-load cost. Ollama's documented preload: an /api/generate call that loads the
# model and (keep_alive -1) pins it until you start playing. num_predict 1 also warms the
# compute path. Loads the same weights the game's /v1 calls use.
warm_model() {
    local m="$1" host="${LOOM_OLLAMA_HOST:-http://localhost:11434}"
    run curl -sS -o /dev/null "${host%/}/api/generate" \
        -d "{\"model\":\"$m\",\"prompt\":\"warm up\",\"stream\":false,\"keep_alive\":-1,\"options\":{\"num_predict\":1}}"
}

# ── 0. Platform ─────────────────────────────────────────────────────────────
UNAME="$(uname -s)"
case "$UNAME" in
    Darwin) OS=macos ;;
    Linux)  OS=linux ;;
    *)      die "unsupported platform '$UNAME' — this wizard targets macOS and Ubuntu/Debian." ;;
esac

say "${BOLD}Loom setup${RESET} ${DIM}(${OS})${RESET}"
[ "$DRY_RUN" = "1" ] && warn "dry-run: install and model-pull steps will be printed, not executed."

# ── 1. Python ≥ 3.11 ────────────────────────────────────────────────────────
step "Checking for Python 3.11+"
PY=""
for cand in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
        if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
            PY="$cand"; break
        fi
    fi
done
if [ -z "$PY" ]; then
    if [ "$OS" = macos ]; then
        die "Python 3.11+ not found. Install it with:  brew install python@3.12"
    else
        die "Python 3.11+ not found. Install it with:  sudo apt-get install -y python3 python3-venv"
    fi
fi
ok "using $("$PY" --version 2>&1) ($(command -v "$PY"))"

# ── 2. Virtualenv ───────────────────────────────────────────────────────────
step "Creating the virtualenv (.venv)"
if [ -d "$VENV_DIR" ]; then
    ok ".venv already exists — reusing it"
else
    run "$PY" -m venv "$VENV_DIR"
    ok "created $VENV_DIR"
fi
VENV_PY="$VENV_DIR/bin/python"
[ "$DRY_RUN" = "1" ] && [ ! -x "$VENV_PY" ] && VENV_PY="$PY"  # dry-run: no real venv

# ── 3. Which extras ─────────────────────────────────────────────────────────
step "Choosing what to install"
say "  The core game runs the server + play client. The authoring workbench"
say "  (${DIM}python -m authoring${RESET}) adds a text-mode world editor with an AI author."
if ask_yn "Include the authoring workbench?" y; then
    EXTRAS="authoring,game"
    ok "will install: loom + game + authoring workbench"
else
    EXTRAS="game"
    ok "will install: loom + game (workbench skipped — add later with pip install -e \".[authoring]\")"
fi

# ── 4. Install ──────────────────────────────────────────────────────────────
step "Installing the package (editable)"
run "$VENV_PY" -m pip install --upgrade pip
run "$VENV_PY" -m pip install -e "$REPO_ROOT[$EXTRAS]"
ok "installed loom-engine[$EXTRAS]"

# ── 5. Backend ──────────────────────────────────────────────────────────────
step "Choosing the AI backend"
say "  ${BOLD}1) OpenRouter${RESET} — hosted inference. No GPU. Needs an API key."
say "  ${BOLD}2) Ollama${RESET}     — local inference on your own machine. No key. Needs disk + RAM/VRAM."
say "  ${DIM}(You can change this any time by editing LOOM_PROVIDER in .env.)${RESET}"
BACKEND="$(ask_val 'Backend — openrouter or ollama' openrouter)"

# Seed .env from the template only if there is no .env yet (never clobber a real one).
if [ ! -f "$ENV_FILE" ]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    ok "created ${ENV_FILE/#$REPO_ROOT\//} from .env.example"
else
    ok "${ENV_FILE/#$REPO_ROOT\//} already exists — updating only the keys below"
fi

case "$BACKEND" in
    openrouter|1|OpenRouter|OPENROUTER)
        set_env_var LOOM_PROVIDER openrouter
        say "  Get a key at ${BLUE}https://openrouter.ai/keys${RESET}"
        read -r -s -p "$(printf '%b?%b OpenRouter API key (leave blank to add later): ' "$BLUE" "$RESET")" OR_KEY || OR_KEY=''
        echo
        if [ -n "$OR_KEY" ]; then
            set_env_var OPENROUTER_API_KEY "$OR_KEY"
        else
            warn "no key entered — set OPENROUTER_API_KEY in ${ENV_FILE/#$REPO_ROOT\//} before running live."
        fi

        # Three model choices, same three roles as the local path — hosted OpenRouter slugs.
        # Not pulled (hosted); an invalid slug surfaces at first inference, not here. Setting all
        # three also overwrites any stale Ollama tags a previous local run left behind.
        step "Choosing models — author, director, NPCs"
        say "  Pick a hosted model for each of the three roles. They can be the same or different."
        MODEL_MENU=("${OPENROUTER_MODELS[@]}")
        MODEL_KIND="OpenRouter slug"
        MODEL_CUSTOM_DEFAULT="qwen/qwen3.6-27b"

        say ""
        say "  ${BOLD}1/3 · authoring agent${RESET} — the workbench's AI world-author (wants reliable judgment)"
        MODEL_DEFAULT="qwen/qwen3.6-27b"
        select_model
        set_env_var LOOM_AUTHOR_MODEL "$REPLY_MODEL"
        ok "authoring agent: $REPLY_MODEL"

        say ""
        say "  ${BOLD}2/3 · game master / director${RESET} — the unseen hand shaping ambient world beats"
        MODEL_DEFAULT="qwen/qwen3.6-27b"
        select_model
        set_env_var LOOM_GM_MODEL "$REPLY_MODEL"
        ok "director: $REPLY_MODEL"

        say ""
        say "  ${BOLD}3/3 · NPC reactions${RESET} — character dialogue in the world (wants speed)"
        MODEL_DEFAULT="qwen/qwen3.6-35b-a3b"
        select_model
        set_env_var LOOM_OPENROUTER_MODEL "$REPLY_MODEL"
        ok "NPCs: $REPLY_MODEL"
        ;;

    ollama|2|Ollama|OLLAMA)
        set_env_var LOOM_PROVIDER ollama
        # 5a. Install the Ollama runtime if it is not already present.
        if command -v ollama >/dev/null 2>&1; then
            ok "ollama already installed ($(ollama --version 2>/dev/null | head -1))"
        else
            step "Installing Ollama"
            if [ "$OS" = macos ]; then
                if command -v brew >/dev/null 2>&1; then
                    run brew install ollama
                else
                    die "Homebrew not found. Install Ollama from https://ollama.com/download, then re-run ./setup.sh"
                fi
            else
                if [ "$DRY_RUN" = "1" ]; then
                    printf '  %b[dry-run]%b %s\n' "$DIM" "$RESET" "curl -fsSL https://ollama.com/install.sh | sh"
                else
                    curl -fsSL https://ollama.com/install.sh | sh
                fi
            fi
            ok "ollama installed"
        fi
        # 5b. Make sure the daemon is reachable (Linux install sets up a systemd
        #     service; on macOS we may need to start it ourselves).
        if [ "$DRY_RUN" != "1" ] && ! ollama list >/dev/null 2>&1; then
            if [ "$OS" = macos ]; then
                warn "starting 'ollama serve' in the background"
                nohup ollama serve >/dev/null 2>&1 &
                for _ in 1 2 3 4 5 6 7 8 9 10; do
                    ollama list >/dev/null 2>&1 && break
                    sleep 1
                done
            fi
            ollama list >/dev/null 2>&1 || warn "ollama daemon not reachable yet — start it with 'ollama serve' before running the game."
        fi
        # 5c. Three model choices — the author, the director, and the NPCs each run their own
        #     model (the same tag for two roles is fine; it is pulled once). The menu notes
        #     weigh authoring reliability against gameplay speed.
        step "Choosing models — author, director, NPCs"
        say "  Pick a model for each of the three roles. They can be the same or different;"
        say "  the menu notes flag which sizes suit authoring (reliable) vs gameplay (fast)."
        # Menu = the portable list, with the Metal-optimised MLX builds surfaced first on a Mac.
        # Per-role suggested defaults: a reliable dense model for the author, a lighter one for
        # the director, and a fast/small one for the NPCs (Mac defaults stay memory-conscious).
        if [ "$OS" = macos ]; then
            MODEL_MENU=("${OLLAMA_DIRECTOR_MODELS_MACOS[@]}" "${OLLAMA_DIRECTOR_MODELS[@]}")
            AUTHOR_DEFAULT="qwen3.5:27b-mlx"
            GM_DEFAULT="qwen3.5:9b-mlx"
            NPC_DEFAULT="qwen3.5:4b-mlx"
        else
            MODEL_MENU=("${OLLAMA_DIRECTOR_MODELS[@]}")
            AUTHOR_DEFAULT="qwen3.6:27b"
            GM_DEFAULT="qwen3.6:27b"
            NPC_DEFAULT="qwen3.5:35b-a3b"
        fi
        MODEL_KIND="Ollama tag"
        MODEL_CUSTOM_DEFAULT="qwen3.6:27b"

        say ""
        say "  ${BOLD}1/3 · authoring agent${RESET} — the workbench's AI world-author (wants reliable judgment)"
        MODEL_DEFAULT="$AUTHOR_DEFAULT"
        select_and_pull_model
        AUTHOR_MODEL="$REPLY_MODEL"
        set_env_var LOOM_AUTHOR_MODEL "$AUTHOR_MODEL"
        ok "authoring agent: $AUTHOR_MODEL"

        say ""
        say "  ${BOLD}2/3 · game master / director${RESET} — the unseen hand shaping ambient world beats"
        MODEL_DEFAULT="$GM_DEFAULT"
        select_and_pull_model
        GM_MODEL="$REPLY_MODEL"
        set_env_var LOOM_GM_MODEL "$GM_MODEL"
        ok "director: $GM_MODEL"

        say ""
        say "  ${BOLD}3/3 · NPC reactions${RESET} — character dialogue in the world (wants speed)"
        MODEL_DEFAULT="$NPC_DEFAULT"
        select_and_pull_model
        NPC_MODEL="$REPLY_MODEL"
        set_env_var LOOM_OLLAMA_MODEL "$NPC_MODEL"
        ok "NPCs: $NPC_MODEL"

        # 5d. Optional warm-up: preload the distinct set of chosen models into memory now, so
        #     the first response isn't slowed by loading weights. Each distinct tag once.
        step "Warming up"
        if ask_yn "Preload the model(s) into Ollama now so the first response is fast?" y; then
            WARMED=" "
            for m in "$AUTHOR_MODEL" "$GM_MODEL" "$NPC_MODEL"; do
                case "$WARMED" in *" $m "*) continue ;; esac
                WARMED="$WARMED$m "
                say "  warming $m (loading into memory — this can take a moment)…"
                warm_model "$m"
            done
            ok "model(s) preloaded and held in memory"
        else
            ok "skipped — the first response will be slower while the model loads"
        fi
        ;;

    *)
        die "unknown backend '$BACKEND' — choose 'openrouter' or 'ollama'."
        ;;
esac

# ── 6. Server port ──────────────────────────────────────────────────────────
# Both the server and the client read LOOM_PORT from .env, so picking one free port
# here wires both. Prefer the port already in .env (a re-run), else 4000; if it is
# taken, suggest the next free one — and let the user override.
step "Choosing the server port"
CUR_PORT="$(grep -E '^LOOM_PORT=' "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2 | tr -d '[:space:]' || true)"
WANT_PORT="${CUR_PORT:-4000}"
if port_free "$WANT_PORT"; then
    SUGGEST="$WANT_PORT"
    ok "port $WANT_PORT is free"
else
    SUGGEST="$(next_free_port "$((WANT_PORT + 1))")"
    warn "port $WANT_PORT is in use — suggesting $SUGGEST"
fi
PORT="$(ask_val 'Server port (used by BOTH the server and the client)' "$SUGGEST")"
if ! printf '%s' "$PORT" | grep -qE '^[0-9]+$'; then
    warn "not a number — using $SUGGEST"
    PORT="$SUGGEST"
fi
if ! port_free "$PORT"; then
    warn "port $PORT looks in use right now — the server may fail to bind (re-run to change it)."
fi
set_env_var LOOM_PORT "$PORT"
ok "server + client will use port $PORT"

# ── 7. Optional self-check ──────────────────────────────────────────────────
step "Self-check"
if ask_yn "Run the offline test suite now? (~1 min, no network)" y; then
    run "$VENV_PY" -m unittest discover -s "$REPO_ROOT/tests"
    ok "offline suite passed"
else
    ok "skipped — run it later with: make test"
fi

# ── 8. Next steps ───────────────────────────────────────────────────────────
step "Setup complete"
cat <<EOF

  ${BOLD}Run it${RESET}

    ${GREEN}make server${RESET}      start the game server
    ${GREEN}make play${RESET}        connect a client and play   (in a second terminal)
    ${GREEN}make workbench${RESET}   open the authoring workbench
    ${GREEN}make test${RESET}        run the offline test suite

  Start ${GREEN}make server${RESET} first, then ${GREEN}make play${RESET} in another terminal to connect.
  Server and client both use port ${BOLD}$PORT${RESET} (from .env).

  ${DIM}Without make — activate the venv first (source .venv/bin/activate):
    game server    python game/main.py
    play client    python client/terminal.py
    workbench      python -m authoring
    tests          python -m unittest discover -s tests${RESET}

  ${GREEN}make${RESET} on its own lists every target. Config lives in ${DIM}.env${RESET}.

EOF
