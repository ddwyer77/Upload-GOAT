#!/usr/bin/env bash
# update_pi.sh  — run this *on the Pi* to refresh Upload GOAT worker
set -euo pipefail

echo "🛠  Updating code..."
cd ~/Upload-GOAT
git pull

echo "🗑  Recreating venv..."
rm -rf ~/uploader-env
python3 -m venv ~/uploader-env
source ~/uploader-env/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r requirements_pi.txt

echo "🔄  Reloading service..."
sudo cp upload-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart upload-worker
sudo systemctl status upload-worker --no-pager

echo "✅  Done — worker restarted!" 