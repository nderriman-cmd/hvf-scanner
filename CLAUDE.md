# HVF Scanner — Claude Code Setup Guide

If a user pastes the setup prompt into Claude Code, follow these instructions exactly.

## Your job
Install and configure the HVF Scanner on the user's machine. Handle every step yourself using bash commands. Never ask the user to run terminal commands themselves — you run them. If something fails, diagnose and fix it before moving on.

## Step-by-step process

### 1. Detect environment
- Check OS: `uname -s` (Darwin=Mac, Linux=Linux)
- Check Python: try `python3 --version` — need 3.9+
- If Python missing on Mac, tell user to install from python.org and wait
- If Python missing on Linux, run `sudo apt install python3 python3-venv python3-pip -y`

### 2. Clone the repo
```bash
git clone https://github.com/nderriman-cmd/hvf-scanner.git ~/hvf-scanner
cd ~/hvf-scanner
```
If `~/hvf-scanner` already exists, run `cd ~/hvf-scanner && git pull` instead.

### 3. Create virtual environment and install dependencies
```bash
cd ~/hvf-scanner
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
```

### 4. Configure Telegram
Ask the user:
- "What is your Telegram Bot Token?" (tell them: open Telegram → @BotFather → /newbot)
- "What is your Telegram Chat ID?" (tell them: open Telegram → @userinfobot → /start)

Write their answers to `~/hvf-scanner/.env`:
```
TELEGRAM_BOT_TOKEN=<their token>
TELEGRAM_CHAT_ID=<their chat id>
```

Send a test message via curl to confirm it works:
```bash
curl -s -X POST "https://api.telegram.org/bot<TOKEN>/sendMessage" \
  -d "chat_id=<CHAT_ID>&text=✅ HVF Scanner connected! Setup complete."
```

If the test fails, check the token and chat ID with the user before continuing.

### 5. Set up auto-start

**On Mac (launchd):**
Write to `~/Library/LaunchAgents/com.hvfscanner.plist` then:
```bash
launchctl unload ~/Library/LaunchAgents/com.hvfscanner.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.hvfscanner.plist
```

**On Linux (systemd):**
Write service file to `/etc/systemd/system/hvf_scanner.service` then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable hvf_scanner
sudo systemctl start hvf_scanner
```

### 6. Confirm it's running
Check logs:
```bash
sleep 5 && tail -20 ~/hvf-scanner/hvf_scanner.log
```
Confirm the scanner started without errors. Tell the user what to expect (Telegram alert when patterns are detected, weekly health check every 7 days).

### 7. Done
Tell the user:
- Scanner is running and will auto-start on login
- To update in future: open Claude Code and say "update my HVF scanner"
- To check status: open Claude Code and say "check if my HVF scanner is running"

## Update command
If user says "update my HVF scanner":
```bash
cd ~/hvf-scanner && git pull && source venv/bin/activate && pip install -r requirements.txt --quiet
```
Then restart the service (launchctl reload on Mac, systemctl restart on Linux).

## Status check command  
If user says "check if my HVF scanner is running":
- Mac: `launchctl list | grep hvfscanner`
- Linux: `systemctl status hvf_scanner`
- Show last 10 lines of `~/hvf-scanner/hvf_scanner.log`

## Common errors and fixes
- `No module named X`: run `source ~/hvf-scanner/venv/bin/activate && pip install -r ~/hvf-scanner/requirements.txt`
- `Permission denied`: add `sudo` to the command
- `Port already in use`: not applicable (scanner uses no ports)
- yfinance download errors: transient — scanner will retry next hour automatically
- Telegram `{"ok":false}`: wrong token or chat ID — re-check with user
