# Deployment: running 24/7 on a cloud server

## Server
| | |
|---|---|
| Provider | Google Cloud Compute Engine, **e2-micro** (free tier) in `us-east1` |
| OS | Ubuntu 24.04 LTS Minimal, 30 GB standard disk |
| Access | SSH key only (`ssh-keygen -t ed25519 -f ~/.ssh/id_sigbot`) |
| Cost | $0 (free tier) |
| Load | ~1 s data fetch per loop, a few % CPU |

The bot is lightweight (I/O-bound), so more CPU/GPU doesn't help.

## First-time setup
Replace `USER@SERVER_IP` with your login and the server's external IP.
```bash
# the minimal image has no rsync; a new VM may hold the apt lock for a few minutes
ssh -i ~/.ssh/id_sigbot USER@SERVER_IP "sudo apt-get update && sudo apt-get install -y rsync"
# copy code + .env + data/ (data/ holds the bot's state)
rsync -a -e "ssh -i ~/.ssh/id_sigbot" --exclude __pycache__ SIG_Competition USER@SERVER_IP:~/
# installs python3-requests / python3-pandas and the daily service, then starts it
ssh -i ~/.ssh/id_sigbot USER@SERVER_IP "cd SIG_Competition && sh deploy/install.sh"
# election-night services + timers
ssh -i ~/.ssh/id_sigbot USER@SERVER_IP "cd SIG_Competition && sh deploy/install_election.sh"
```
Never run the bot on two machines at once: they would share one rate limit and trade against each other.

## systemd units ([`deploy/`](../deploy/))
| Unit | Runs | When |
|---|---|---|
| `sigbot.service` | daily bot (`--mode multi …`) | always; restarts on crash, **not** after the kill switch |
| `sigbot-unwind.timer` | sell the remaining hedge only at or above cost | hourly until the switch |
| `sigbot-rehearsal.timer` | refresh the Polymarket mapping (never shrinks it) + dry-run rehearsal | Wed Oct 28, 10:00am ET |
| `sigbot-switch.timer` → `switch_to_election.sh` | stop the daily bot, reset the price baseline, start `sigbot-election.service` | **Tue Nov 3, 5:45pm ET** (22:45 UTC) |
| `sigbot-stop.timer` | stop the election bot | Wed Nov 4, 12:01pm ET (trading deadline +1 min) |

Times are set in UTC. Daylight saving time ends Nov 1, so election night is EST (UTC−5).

## Operations
```bash
S="ssh -i ~/.ssh/id_sigbot USER@SERVER_IP"
$S "tail -f SIG_Competition/data/bot_multi.log"                       # watch (daily)
$S "tail -f SIG_Competition/data/bot_election.log"                    # watch (election night)
$S "cd SIG_Competition && python3 -m sigcup.botreport"                # P&L
$S "cd SIG_Competition && python3 -m sigcup.standing"                 # leaderboard percentiles
$S "touch SIG_Competition/data/STOP"                                  # stop: cancels orders, exits
$S "rm -f SIG_Competition/data/STOP && sudo systemctl restart sigbot" # start again
$S "systemctl list-timers --all | grep sigbot"                        # scheduled jobs
$S "cd SIG_Competition && python3 -m sigcup.call 'Georgia Senate' democratic"   # election-night race call
```
**Code updates:** `rsync` the `sigcup/` folder, then restart the service with the stop-then-start pattern above.

## Lessons from setup
- The Azure for Students sign-up link was down and DigitalOcean's student offer had ended. The GCP free tier worked.
- A new VM runs automatic updates that hold the apt lock for minutes, and the minimal image lacks `rsync` and `fuser`.
- Running a second process (rehearsal, history download) against the same account slows the live bot through the shared rate limit. Keep them short.
