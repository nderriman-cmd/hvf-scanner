#!/usr/bin/env bash
# ============================================================================
#  HVF Scanner — One-Command Setup
#  Works on: macOS, Ubuntu/Debian Linux, Windows WSL2
#
#  Usage (after repo is public on GitHub):
#    curl -fsSL https://raw.githubusercontent.com/nderriman-cmd/hvf-scanner/main/setup.sh | bash
#
#  Or if you've already cloned the repo:
#    bash setup.sh
# ============================================================================

set -e

REPO_URL="https://github.com/nderriman-cmd/hvf-scanner.git"
INSTALL_DIR="$HOME/hvf-scanner"
VENV_DIR="$INSTALL_DIR/venv"
PYTHON_MIN="3.9"

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

banner() {
    echo ""
    echo -e "${BOLD}${BLUE}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}${BLUE}║          HVF SCANNER — Setup Wizard                         ║${NC}"
    echo -e "${BOLD}${BLUE}║          Hunt Volatility Funnel Signal Scanner               ║${NC}"
    echo -e "${BOLD}${BLUE}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

step()  { echo -e "\n${BOLD}${CYAN}▶ $1${NC}"; }
ok()    { echo -e "  ${GREEN}✓ $1${NC}"; }
warn()  { echo -e "  ${YELLOW}⚠ $1${NC}"; }
err()   { echo -e "  ${RED}✗ $1${NC}"; exit 1; }
info()  { echo -e "  ${BLUE}ℹ $1${NC}"; }
ask()   { echo -e "\n${BOLD}$1${NC}"; }

# ── OS detection ─────────────────────────────────────────────────────────────
detect_os() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        OS="mac"
    elif grep -qi microsoft /proc/version 2>/dev/null; then
        OS="wsl"
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        OS="linux"
    else
        OS="unknown"
    fi
}

# ── Check Python ─────────────────────────────────────────────────────────────
check_python() {
    step "Checking Python version"

    PYTHON=""
    for cmd in python3.11 python3.10 python3.9 python3; do
        if command -v "$cmd" &>/dev/null; then
            VER=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
            MAJOR=$(echo "$VER" | cut -d. -f1)
            MINOR=$(echo "$VER" | cut -d. -f2)
            if [[ "$MAJOR" -ge 3 && "$MINOR" -ge 9 ]]; then
                PYTHON="$cmd"
                ok "Found Python $VER ($cmd)"
                break
            fi
        fi
    done

    if [[ -z "$PYTHON" ]]; then
        err "Python 3.9+ is required but not found.

  macOS:   brew install python3
  Ubuntu:  sudo apt install python3.11 python3.11-venv python3-pip
  Windows: Install Python from https://python.org/downloads/
           (make sure to tick 'Add to PATH')"
    fi
}

# ── Clone or update repo ──────────────────────────────────────────────────────
get_code() {
    step "Getting HVF Scanner code"

    if [[ -d "$INSTALL_DIR/.git" ]]; then
        info "Existing install found at $INSTALL_DIR — pulling latest..."
        cd "$INSTALL_DIR"
        git pull origin main
        ok "Code updated"
    else
        if [[ -d "$INSTALL_DIR" ]]; then
            warn "Directory $INSTALL_DIR exists but is not a git repo"
            ask "Remove and re-clone? [y/N]"
            read -r REPLY
            if [[ "$REPLY" =~ ^[Yy]$ ]]; then
                rm -rf "$INSTALL_DIR"
            else
                err "Aborted. Please remove $INSTALL_DIR manually and re-run."
            fi
        fi
        info "Cloning into $INSTALL_DIR ..."
        git clone "$REPO_URL" "$INSTALL_DIR"
        ok "Code downloaded"
    fi

    cd "$INSTALL_DIR"
}

# ── Virtual environment ───────────────────────────────────────────────────────
setup_venv() {
    step "Setting up Python virtual environment"

    if [[ ! -d "$VENV_DIR" ]]; then
        "$PYTHON" -m venv "$VENV_DIR"
        ok "Virtual environment created"
    else
        ok "Virtual environment already exists"
    fi

    # Activate
    source "$VENV_DIR/bin/activate"

    # Upgrade pip silently
    pip install --upgrade pip --quiet

    # Install dependencies
    info "Installing dependencies (pandas, yfinance, scipy...)..."
    pip install -r requirements.txt --quiet
    ok "Dependencies installed"
}

# ── Telegram setup ────────────────────────────────────────────────────────────
setup_telegram() {
    step "Telegram Bot Configuration"

    echo ""
    echo -e "  The scanner sends alerts to your Telegram account."
    echo -e "  You need a Bot Token and your Chat ID."
    echo ""
    echo -e "  ${BOLD}How to get these (2 minutes):${NC}"
    echo -e "  1. Open Telegram → search for ${CYAN}@BotFather${NC}"
    echo -e "  2. Send: /newbot  → follow prompts → copy the token"
    echo -e "  3. Search for ${CYAN}@userinfobot${NC} → send /start → copy your Chat ID"
    echo ""

    if [[ -f "$INSTALL_DIR/.env" ]]; then
        warn ".env already exists — skipping Telegram setup"
        info "Edit $INSTALL_DIR/.env to change credentials"
        return
    fi

    while true; do
        ask "Enter your Telegram Bot Token:"
        read -r TG_TOKEN
        if [[ -n "$TG_TOKEN" && "$TG_TOKEN" == *":"* ]]; then
            break
        fi
        warn "Token looks invalid — it should contain a colon (:). Try again."
    done

    while true; do
        ask "Enter your Telegram Chat ID (numbers only, may start with -):"
        read -r TG_CHAT
        if [[ -n "$TG_CHAT" ]]; then
            break
        fi
        warn "Chat ID cannot be empty. Try again."
    done

    cat > "$INSTALL_DIR/.env" << EOF
TELEGRAM_BOT_TOKEN=${TG_TOKEN}
TELEGRAM_CHAT_ID=${TG_CHAT}
EOF

    ok ".env file created"

    # Quick test
    echo ""
    info "Sending test message to Telegram..."
    RESP=$(curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
        -d "chat_id=${TG_CHAT}" \
        -d "text=✅ HVF Scanner connected! Setup complete." \
        -d "parse_mode=HTML" 2>/dev/null || true)

    if echo "$RESP" | grep -q '"ok":true'; then
        ok "Test message sent! Check your Telegram."
    else
        warn "Could not send test message — check your token and chat ID in .env"
    fi
}

# ── Deployment choice ─────────────────────────────────────────────────────────
choose_deployment() {
    step "Deployment Mode"

    echo ""
    echo -e "  How would you like to run the HVF Scanner?"
    echo ""
    echo -e "  ${BOLD}[1] Local — run on this machine${NC}"
    echo -e "      Runs while your computer is on. Free."
    echo -e "      Mac: auto-starts on login via launchd"
    echo -e "      Linux: auto-starts via systemd"
    echo ""
    echo -e "  ${BOLD}[2] Free Cloud Server (Oracle Always Free)${NC}"
    echo -e "      Runs 24/7 in the cloud. Always free tier."
    echo -e "      Requires an Oracle Cloud account (free, no credit card needed)"
    echo ""
    echo -e "  ${BOLD}[3] Paid Cloud Server (Hetzner CX22 — ~€4/month)${NC}"
    echo -e "      Best option for 24/7 reliability. What the creator uses."
    echo -e "      Hetzner is the fastest/cheapest reliable VPS in Europe."
    echo ""
    echo -e "  ${BOLD}[4] Manual — I'll run it myself${NC}"
    echo -e "      Just set up the code, I'll handle the rest."
    echo ""

    while true; do
        ask "Choose [1/2/3/4]:"
        read -r DEPLOY_CHOICE
        case "$DEPLOY_CHOICE" in
            1|2|3|4) break ;;
            *) warn "Please enter 1, 2, 3, or 4" ;;
        esac
    done
}

# ── Local deployment ──────────────────────────────────────────────────────────
setup_local() {
    step "Setting up local auto-start"

    if [[ "$OS" == "mac" ]]; then
        PLIST="$HOME/Library/LaunchAgents/com.hvfscanner.plist"
        cat > "$PLIST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.hvfscanner</string>
    <key>ProgramArguments</key>
    <array>
        <string>${VENV_DIR}/bin/python3</string>
        <string>${INSTALL_DIR}/hvf_scanner.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${INSTALL_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${INSTALL_DIR}/hvf_scanner.log</string>
    <key>StandardErrorPath</key>
    <string>${INSTALL_DIR}/hvf_scanner.log</string>
</dict>
</plist>
EOF
        launchctl unload "$PLIST" 2>/dev/null || true
        launchctl load "$PLIST"
        ok "Scanner registered as macOS launch agent"
        ok "Will auto-start on login and restart if it crashes"

    elif [[ "$OS" == "linux" || "$OS" == "wsl" ]]; then
        SERVICE_FILE="/etc/systemd/system/hvf_scanner.service"
        sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=HVF Scanner
After=network.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=${VENV_DIR}/bin/python3 ${INSTALL_DIR}/hvf_scanner.py
Restart=always
RestartSec=30
StandardOutput=append:${INSTALL_DIR}/hvf_scanner.log
StandardError=append:${INSTALL_DIR}/hvf_scanner.log

[Install]
WantedBy=multi-user.target
EOF
        sudo systemctl daemon-reload
        sudo systemctl enable hvf_scanner
        sudo systemctl start hvf_scanner
        ok "Scanner registered as systemd service"
        ok "Will auto-start on boot and restart if it crashes"
    fi

    echo ""
    echo -e "  ${GREEN}${BOLD}Scanner is now running!${NC}"
    echo -e "  Logs: ${CYAN}tail -f ${INSTALL_DIR}/hvf_scanner.log${NC}"
    echo -e "  Stop: ${CYAN}bash ${INSTALL_DIR}/update.sh stop${NC}"
}

# ── Oracle Cloud instructions ─────────────────────────────────────────────────
setup_oracle() {
    step "Oracle Cloud Free Tier Setup Guide"

    echo ""
    echo -e "  Oracle Always Free gives you a permanently free Linux server."
    echo -e "  No credit card required. Follow these steps:"
    echo ""
    echo -e "  ${BOLD}1. Create account:${NC}"
    echo -e "     → cloud.oracle.com/free"
    echo -e "     → Sign up (free, no card needed for Always Free tier)"
    echo ""
    echo -e "  ${BOLD}2. Create a Compute Instance:${NC}"
    echo -e "     → Compute → Instances → Create Instance"
    echo -e "     → Shape: VM.Standard.A1.Flex (ARM, Always Free)"
    echo -e "     → Image: Ubuntu 22.04"
    echo -e "     → Generate and download SSH key pair"
    echo -e "     → Click Create"
    echo ""
    echo -e "  ${BOLD}3. Once your server is running, SSH in:${NC}"
    echo -e "     ${CYAN}ssh -i ~/your-key.pem ubuntu@YOUR_SERVER_IP${NC}"
    echo ""
    echo -e "  ${BOLD}4. Run this setup command on the server:${NC}"
    echo -e "     ${CYAN}curl -fsSL ${REPO_URL/%.git/}/raw/main/setup.sh | bash${NC}"
    echo ""
    echo -e "  ${BOLD}5. Choose option [1] Local when prompted${NC}"
    echo -e "     (the 'local' on your cloud server = 24/7 cloud running)"
    echo ""
    echo -e "  ${YELLOW}Tip: Oracle's ARM servers are surprisingly fast and completely free forever.${NC}"
    echo ""

    info "Your .env file with Telegram credentials has been saved to:"
    info "$INSTALL_DIR/.env"
    info "You'll need to copy these to your server:"
    echo ""
    echo -e "  ${CYAN}scp -i ~/your-key.pem $INSTALL_DIR/.env ubuntu@YOUR_SERVER_IP:~/hvf-scanner/.env${NC}"
}

# ── Hetzner instructions ──────────────────────────────────────────────────────
setup_hetzner() {
    step "Hetzner VPS Setup Guide"

    echo ""
    echo -e "  Hetzner is the most cost-effective reliable VPS (~€4/month)."
    echo -e "  This is exactly what the scanner creator uses."
    echo ""
    echo -e "  ${BOLD}1. Create account:${NC}"
    echo -e "     → hetzner.com → Cloud → Sign up"
    echo ""
    echo -e "  ${BOLD}2. Create a Server:${NC}"
    echo -e "     → New Project → Add Server"
    echo -e "     → Location: Helsinki or Nuremberg (cheapest)"
    echo -e "     → Image: Ubuntu 24.04"
    echo -e "     → Type: Shared CPU → CX22 (2 vCPU, 4GB RAM) = ~€4.15/mo"
    echo -e "     → SSH Keys: paste your public key (or use password)"
    echo -e "     → Click Create & Buy Now"
    echo ""
    echo -e "  ${BOLD}3. SSH into your new server:${NC}"
    echo -e "     ${CYAN}ssh root@YOUR_SERVER_IP${NC}"
    echo ""
    echo -e "  ${BOLD}4. Run setup on the server:${NC}"
    echo -e "     ${CYAN}apt update && apt install -y python3 python3-venv git curl${NC}"
    echo -e "     ${CYAN}curl -fsSL https://raw.githubusercontent.com/nderriman-cmd/hvf-scanner/main/setup.sh | bash${NC}"
    echo ""
    echo -e "  ${BOLD}5. Choose option [1] Local when prompted${NC}"
    echo ""
    echo -e "  ${BOLD}6. Copy your Telegram credentials to the server:${NC}"
    echo -e "     ${CYAN}scp $INSTALL_DIR/.env root@YOUR_SERVER_IP:~/hvf-scanner/.env${NC}"
    echo ""
    echo -e "  ${GREEN}That's it — your scanner will run 24/7 and auto-restart if it crashes.${NC}"
}

# ── Manual mode ───────────────────────────────────────────────────────────────
setup_manual() {
    step "Manual Setup Complete"
    echo ""
    echo -e "  Everything is installed. To run manually:"
    echo ""
    echo -e "  ${CYAN}cd $INSTALL_DIR${NC}"
    echo -e "  ${CYAN}source venv/bin/activate${NC}"
    echo -e "  ${CYAN}python3 hvf_scanner.py${NC}"
    echo ""
    echo -e "  Or use the convenience script:"
    echo -e "  ${CYAN}bash $INSTALL_DIR/run.sh${NC}"
}

# ── Create run.sh convenience script ─────────────────────────────────────────
create_run_script() {
    cat > "$INSTALL_DIR/run.sh" << 'EOF'
#!/usr/bin/env bash
# Quick-start the HVF Scanner
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
source venv/bin/activate
echo "Starting HVF Scanner... (Ctrl+C to stop)"
python3 hvf_scanner.py
EOF
    chmod +x "$INSTALL_DIR/run.sh"
}

# ── Final summary ─────────────────────────────────────────────────────────────
print_summary() {
    echo ""
    echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}${GREEN}║              Setup Complete!                                ║${NC}"
    echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  ${BOLD}Install location:${NC}  $INSTALL_DIR"
    echo -e "  ${BOLD}Config file:${NC}       $INSTALL_DIR/.env"
    echo -e "  ${BOLD}Logs:${NC}              $INSTALL_DIR/hvf_scanner.log"
    echo ""
    echo -e "  ${BOLD}Useful commands:${NC}"
    echo -e "  ${CYAN}bash $INSTALL_DIR/run.sh${NC}        — run scanner manually"
    echo -e "  ${CYAN}bash $INSTALL_DIR/update.sh${NC}     — pull latest updates"
    echo -e "  ${CYAN}bash $INSTALL_DIR/update.sh status${NC} — check if scanner is running"
    echo -e "  ${CYAN}bash $INSTALL_DIR/update.sh stop${NC}   — stop the scanner"
    echo ""
    echo -e "  ${BOLD}What to expect:${NC}"
    echo -e "  • Scanner checks all assets every hour"
    echo -e "  • Telegram alerts when HVF pattern is detected"
    echo -e "  • 3 alert stages: 📐 Forming  ⚡ Near  🚨 Breakout"
    echo -e "  • Weekly health check confirms scanner is alive"
    echo ""
    echo -e "  ${YELLOW}Questions? Join the Patreon community for support.${NC}"
    echo ""
}

# ── Main ─────────────────────────────────────────────────────────────────────
main() {
    banner
    detect_os
    check_python
    get_code
    setup_venv
    setup_telegram
    choose_deployment
    create_run_script

    case "$DEPLOY_CHOICE" in
        1) setup_local ;;
        2) setup_oracle ;;
        3) setup_hetzner ;;
        4) setup_manual ;;
    esac

    print_summary
}

main "$@"
