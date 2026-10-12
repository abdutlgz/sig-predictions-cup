#!/bin/sh
# Run on the server from the project folder:  sh deploy/install.sh
# Installs Python packages and the systemd service for the current user and folder, then starts the bot.
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(id -un)"
sudo apt-get update -q
sudo apt-get install -y -q python3-requests python3-pandas
sed -e "s|/root/SIG_Competition|$DIR|" \
    -e "s|^\[Service\]|[Service]\nUser=$USER_NAME|" \
    "$DIR/deploy/sigbot.service" | sudo tee /etc/systemd/system/sigbot.service >/dev/null
rm -f "$DIR/data/STOP"
sudo systemctl daemon-reload
sudo systemctl enable --now sigbot
sleep 3
systemctl --no-pager status sigbot | head -5
echo "Log: tail -f $DIR/data/bot_multi.log"
