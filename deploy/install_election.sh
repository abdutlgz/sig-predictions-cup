#!/bin/sh
# Run on the server from the project folder: sh deploy/install_election.sh
# Installs the election-night services and timers for the current user and folder.
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(id -un)"
for u in sigbot-election.service sigbot-rehearsal.service sigbot-unwind.service; do  # run as the user
  sed -e "s|/root/SIG_Competition|$DIR|" -e "s|^\[Service\]|[Service]\nUser=$USER_NAME|" "$DIR/deploy/$u" \
    | sudo tee "/etc/systemd/system/$u" >/dev/null
done
for u in sigbot-switch.service sigbot-stop.service sigbot-switch.timer sigbot-stop.timer \
         sigbot-rehearsal.timer sigbot-unwind.timer; do                               # run as root
  sed -e "s|/root/SIG_Competition|$DIR|" "$DIR/deploy/$u" | sudo tee "/etc/systemd/system/$u" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now sigbot-switch.timer sigbot-stop.timer sigbot-rehearsal.timer sigbot-unwind.timer
systemctl list-timers --all --no-pager | grep -E "NEXT|sigbot"
