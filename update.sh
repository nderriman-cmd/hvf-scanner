#!/usr/bin/env bash
# ============================================================================
#  HVF Scanner — Update & Control Script
#
#  Usage:
#    bash update.sh           — pull latest code + restart scanner
#    bash update.sh status    — check if scanner is running
#    bash update.sh stop      — stop the scanner
#    bash update.sh start     — start the scanner
#    bash update.sh logs      — tail live logs
#    bash update.sh version   — show current version
# ============================================================================

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$INSTALL_DIR/venv"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

step() { echo -e "\n${BOLD}${CYAN}▶ $1${NC}"; }
ok()   { echo -e "  ${GREEN}✓ $1${NC}"; }
warn() { echo -e "  ${YELLOW}⚠ $1${NC}"; }
err()  { echo -e "  ${RED}✗ $1${NC}"; exit 1; }
info() { echo -e "  ${BLUE}ℹ $1${NC}"; }

# ── Detect how the scanner is running ────────────────────────────────────────
detect_mode() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        MODE="launchd"
        PLIST="$HOME/Library/LaunchAgents/com.hvfscanner.plist"
    elif command -v systemctl &>/dev/null && systemctl list-units --type=service 2>/dev/null | grep -q hvf_scanner; then
        MODE="systemd"
    else
        MODE="manual"
    fi
}

# ── Status ────────────────────────────────────────────────────────────────────
cmd_status() {
    step "HVF Scanner Status"
    detect_mode

    if [[ "$MODE" == "launchd" ]]; then
        if launchctl list | grep -q "com.hvfscanner"; then
            ok "Running (macOS launchd)"
        else
            warn "Not running"
        fi
    elif [[ "$MODE" == "systemd" ]]; then
        systemctl status hvf_scanner --no-pager 2>/dev/null || warn "Not running"
    else
        PID=$(pgrep -f "hvf_scanner.py" 2>/dev/null || true)
        if [[ -n "$PID" ]]; then
            ok "Running (PID $PID)"
        else
            warn "Not running"
        fi
    fi

    echo ""
    if [[ -f "$INSTALL_DIR/hvf_scanner.log" ]]; then
        info "Last 5 log lines:"
        tail -5 "$INSTALL_DIR/hvf_scanner.log" | sed 's/^/    /'
    fi
}

# ── Stop ──────────────────────────────────────────────────────────────────────
cmd_stop() {
    step "Stopping HVF Scanner"
    detect_mode

    if [[ "$MODE" == "launchd" ]]; then
        launchctl unload "$PLIST" 2>/dev/null && ok "Stopped (launchd)" || warn "Was not running"
    elif [[ "$MODE" == "systemd" ]]; then
        sudo systemctl stop hvf_scanner && ok "Stopped (systemd)" || warn "Was not running"
    else
        pkill -f "hvf_scanner.py" 2>/dev/null && ok "Stopped" || warn "Was not running"
    fi
}

# ── Start ─────────────────────────────────────────────────────────────────────
cmd_start() {
    step "Starting HVF Scanner"
    detect_mode

    if [[ "$MODE" == "launchd" ]]; then
        launchctl load "$PLIST" 2>/dev/null && ok "Started (launchd)" || err "Failed to start"
    elif [[ "$MODE" == "systemd" ]]; then
        sudo systemctl start hvf_scanner && ok "Started (systemd)" || err "Failed to start"
    else
        source "$VENV_DIR/bin/activate"
        nohup python3 "$INSTALL_DIR/hvf_scanner.py" >> "$INSTALL_DIR/hvf_scanner.log" 2>&1 &
        ok "Started in background (PID $!)"
    fi
}

# ── Logs ──────────────────────────────────────────────────────────────────────
cmd_logs() {
    LOG="$INSTALL_DIR/hvf_scanner.log"
    if [[ -f "$LOG" ]]; then
        echo -e "${CYAN}Tailing logs (Ctrl+C to stop)...${NC}\n"
        tail -f "$LOG"
    else
        warn "No log file found at $LOG"
    fi
}

# ── Version ───────────────────────────────────────────────────────────────────
cmd_version() {
    step "Current Version"
    cd "$INSTALL_DIR"
    if git rev-parse --git-dir &>/dev/null; then
        COMMIT=$(git log -1 --format="%h %s" 2>/dev/null)
        DATE=$(git log -1 --format="%cd" --date=short 2>/dev/null)
        info "Commit: $COMMIT"
        info "Date:   $DATE"
        echo ""
        REMOTE=$(git log HEAD..origin/main --oneline 2>/dev/null | wc -l | tr -d ' ')
        if [[ "$REMOTE" -gt 0 ]]; then
            warn "$REMOTE update(s) available — run: bash update.sh"
        else
            ok "Up to date"
        fi
    else
        warn "Not a git repository — cannot check version"
    fi
}

# ── Update ────────────────────────────────────────────────────────────────────
cmd_update() {
    echo ""
    echo -e "${BOLD}${BLUE}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}${BLUE}║          HVF Scanner — Updating                             ║${NC}"
    echo -e "${BOLD}${BLUE}╚══════════════════════════════════════════════════════════════╝${NC}"

    cd "$INSTALL_DIR"

    # Check for git
    if ! git rev-parse --git-dir &>/dev/null; then
        err "Not a git repository. Please re-run setup.sh to reinstall."
    fi

    # Show what's coming
    step "Checking for updates"
    git fetch origin main --quiet 2>/dev/null

    CHANGES=$(git log HEAD..origin/main --oneline 2>/dev/null)
    if [[ -z "$CHANGES" ]]; then
        ok "Already up to date — nothing to do"
        echo ""
        cmd_status
        exit 0
    fi

    echo ""
    echo -e "  ${BOLD}Incoming changes:${NC}"
    echo "$CHANGES" | while read -r line; do
        echo -e "    ${CYAN}→${NC} $line"
    done

    # Stop scanner before update
    step "Stopping scanner for update"
    cmd_stop 2>/dev/null || true
    sleep 2

    # Pull changes
    step "Pulling latest code"
    git pull origin main
    ok "Code updated"

    # Update dependencies (only installs new/changed packages)
    step "Updating dependencies"
    source "$VENV_DIR/bin/activate"
    pip install -r requirements.txt --quiet --upgrade
    ok "Dependencies up to date"

    # Restart
    step "Restarting scanner"
    cmd_start

    echo ""
    echo -e "  ${GREEN}${BOLD}Update complete!${NC}"
    echo ""
    cmd_version
    echo ""
    info "Logs: bash $INSTALL_DIR/update.sh logs"
}

# ── Entry point ───────────────────────────────────────────────────────────────
case "${1:-update}" in
    update|"")  cmd_update ;;
    status)     cmd_status ;;
    stop)       cmd_stop ;;
    start)      cmd_start ;;
    restart)    cmd_stop; sleep 1; cmd_start ;;
    logs)       cmd_logs ;;
    version)    cmd_version ;;
    *)
        echo "Usage: bash update.sh [update|status|start|stop|restart|logs|version]"
        echo ""
        echo "  update   — pull latest code and restart (default)"
        echo "  status   — show if scanner is running"
        echo "  start    — start the scanner"
        echo "  stop     — stop the scanner"
        echo "  restart  — stop then start"
        echo "  logs     — tail live log output"
        echo "  version  — show version and check for updates"
        ;;
esac
