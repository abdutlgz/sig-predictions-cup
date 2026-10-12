#!/bin/sh
# Daily bot -> election-night bot. Run (as root) by sigbot-switch.timer on Nov 3, 5:45pm ET.
DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$DIR/data/switch.log"
echo "$(date -u) switching to election mode" >> "$LOG"
systemctl disable --now sigbot-unwind.timer >> "$LOG" 2>&1
touch "$DIR/data/STOP"                       # daily bot cancels its orders and exits
i=0; while systemctl is-active -q sigbot && [ $i -lt 90 ]; do sleep 2; i=$((i+1)); done
systemctl stop sigbot; systemctl disable sigbot >> "$LOG" 2>&1
rm -f "$DIR/data/STOP" "$DIR/data/night_baseline.json"   # fresh price baseline for "moved since 5:45pm"
systemctl enable --now sigbot-election >> "$LOG" 2>&1
sleep 20
echo "$(date -u) daily: $(systemctl is-active sigbot), election: $(systemctl is-active sigbot-election)" >> "$LOG"
