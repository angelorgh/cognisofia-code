#!/bin/bash
# =============================================================
# bluetooth_agent_setup.sh
# Configures BlueZ for auto-accepting BLE connections (no pairing
# confirmation required). Designed to run on Raspberry Pi after
# every reboot, before launching dummynirs.py.
#
# Install as a systemd service (see bottom of this file).
# =============================================================

set -e

LOG_TAG="bluetooth-setup"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    logger -t "$LOG_TAG" "$1"
}

# -------------------------------------------------------------
# 1. Wait for bluetoothd to be fully up
# -------------------------------------------------------------
log "Waiting for bluetoothd..."
for i in $(seq 1 20); do
    if systemctl is-active --quiet bluetooth; then
        log "bluetoothd is active."
        break
    fi
    sleep 1
done

if ! systemctl is-active --quiet bluetooth; then
    log "ERROR: bluetoothd did not start. Attempting to start it..."
    systemctl start bluetooth
    sleep 3
fi

# Give the adapter a moment to initialise
sleep 2

# -------------------------------------------------------------
# 2. Power on and configure the adapter
# -------------------------------------------------------------
log "Powering on BT adapter..."
bluetoothctl power on

log "Making adapter discoverable and pairable..."
bluetoothctl discoverable on
bluetoothctl pairable on

# PairableTimeout=0 and DiscoverableTimeout=0 should already be
# set in /etc/bluetooth/main.conf, but set them here as well to
# be safe.
bluetoothctl discoverable-timeout 0  2>/dev/null || true
bluetoothctl pairable-timeout 0      2>/dev/null || true

# -------------------------------------------------------------
# 3. Kill any existing bt-agent or bluetoothctl agent processes
# -------------------------------------------------------------
log "Stopping any existing agent processes..."
pkill -f 'bt-agent'       2>/dev/null || true
pkill -f 'bluetoothctl.*agent' 2>/dev/null || true
sleep 1

# -------------------------------------------------------------
# 4. Register a persistent NoInputNoOutput agent
#
#    Preference order:
#      a) bt-agent (from bluez-tools) — most reliable
#      b) bluetoothctl built-in agent  — fallback
# -------------------------------------------------------------
if command -v bt-agent &>/dev/null; then
    log "Starting bt-agent (NoInputNoOutput) in background..."
    # -c NoInputNoOutput = "Just Works" pairing, no PIN/confirmation
    bt-agent -c NoInputNoOutput &
    BT_AGENT_PID=$!
    log "bt-agent started (PID $BT_AGENT_PID)"
else
    log "bt-agent not found (install with: sudo apt install bluez-tools)"
    log "Falling back to bluetoothctl agent..."

    # Use bluetoothctl in a background subshell that keeps the
    # agent alive; the process must stay running.
    (
        echo -e "agent NoInputNoOutput\ndefault-agent\n" | bluetoothctl
    ) &
    log "bluetoothctl agent registered (PID $!)"
fi

# -------------------------------------------------------------
# 5. Make our agent the default
# -------------------------------------------------------------
sleep 1
log "Setting default agent..."
bluetoothctl default-agent 2>/dev/null || true

# -------------------------------------------------------------
# 6. Final status
# -------------------------------------------------------------
log "Bluetooth setup complete."
log "Adapter status:"
bluetoothctl show | grep -E 'Name|Powered|Discoverable|Pairable|Address'

exit 0


# =============================================================
# INSTALLATION AS A SYSTEMD SERVICE
# =============================================================
#
# 1. Copy this script to /usr/local/bin/ and make it executable:
#
#       sudo cp bluetooth_agent_setup.sh /usr/local/bin/bluetooth_agent_setup.sh
#       sudo chmod +x /usr/local/bin/bluetooth_agent_setup.sh
#
# 2. Create the service file:
#
#       sudo nano /etc/systemd/system/bluetooth-agent-setup.service
#
#    Paste the contents between the --- markers below:
#
# ---
# [Unit]
# Description=BlueZ NoInputNoOutput agent setup for NIRDuino emulator
# After=bluetooth.service
# Requires=bluetooth.service
#
# [Service]
# Type=forking
# ExecStart=/usr/local/bin/bluetooth_agent_setup.sh
# RemainAfterExit=yes
# Restart=on-failure
# RestartSec=5
#
# [Install]
# WantedBy=multi-user.target
# ---
#
# 3. Enable and start the service:
#
#       sudo systemctl daemon-reload
#       sudo systemctl enable bluetooth-agent-setup.service
#       sudo systemctl start bluetooth-agent-setup.service
#
# 4. Check status:
#
#       sudo systemctl status bluetooth-agent-setup.service
#       journalctl -u bluetooth-agent-setup.service -n 30
#
# 5. To install bluez-tools (provides bt-agent) if not present:
#
#       sudo apt install bluez-tools
#
# =============================================================
