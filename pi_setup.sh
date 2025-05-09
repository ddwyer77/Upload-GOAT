#!/usr/bin/env bash
# Raspberry Pi setup for Upload GOAT headless worker
set -euo pipefail

# Update and install prerequisites
sudo apt update && sudo apt install -y python3-venv python3-pip ffmpeg

# Create virtual environment and install dependencies
python3 -m venv ~/uploader-env
source ~/uploader-env/bin/activate
pip install -r requirements_pi.txt
echo "Installed slim requirements (no PyQt5) for headless worker"

# Create queue and logs directories
mkdir -p ~/upload_queue ~/upload_logs

# Copy .env example and prompt for API key
if [ ! -f ~/.env ]; then
  cp .env.example ~/.env
  echo "# Please edit ~/.env and add your Upload-Post API_KEY" >> ~/.env
  echo "To edit, run: nano ~/.env"
fi

echo "Raspberry Pi setup complete." 