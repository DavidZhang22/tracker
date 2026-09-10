#!/usr/bin/env bash
set -euo pipefail
cd /home/azureuser/tracker
# Preserve key-based administrative access; Docker publishes only web ports.
sudo tee /etc/ssh/sshd_config.d/00-catchup.conf >/dev/null <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
EOF
sudo /usr/sbin/sshd -t
sudo systemctl reload ssh
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
sudo install -m 0644 deploy/catchup-backup.service /etc/systemd/system/catchup-backup.service
sudo install -m 0644 deploy/catchup-backup.timer /etc/systemd/system/catchup-backup.timer
sudo systemctl daemon-reload
sudo systemctl enable --now catchup-backup.timer
sudo systemctl start catchup-backup.service
sudo systemctl list-timers catchup-backup.timer --no-pager
