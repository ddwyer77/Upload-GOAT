"""Headless worker that uploads queued videos to Upload-Post."""

import json
import time
import pathlib
import datetime
import traceback
from uploader import UploadPostClient
from dotenv import load_dotenv
import os

# Load API key from .env
load_dotenv()
API_KEY = os.getenv("API_KEY", "")
API = UploadPostClient(API_KEY)

QUEUE_DIR = pathlib.Path("/home/pi/upload_queue")
LOG_FILE = pathlib.Path("/home/pi/upload_logs/worker_log.jsonl")
SLEEP_SEC = 30


def log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a") as fp:
        fp.write(json.dumps(entry) + "\n")


while True:
    for task_path in QUEUE_DIR.glob("*.task.json"):
        try:
            task = json.loads(task_path.read_text())
            sched = datetime.datetime.fromisoformat(task["scheduled_at"])
            now = datetime.datetime.now(sched.tzinfo)
            if now < sched:
                continue  # not time yet

            video_path = task_path.with_name(task["video"])
            # Attempt upload with retries and exponential back-off
            success = False
            for attempt in range(1, 4):  # max 3 tries
                try:
                    API.upload_video(
                        video_path=str(video_path),
                        caption=task["caption"],
                        user=task["user"]
                    )
                    success = True
                    log({**task, "status": "ok", "attempt": attempt, "timestamp": datetime.datetime.now().isoformat()})
                    break
                except Exception as e:
                    # Log retry attempt
                    log({**task, "status": "retry", "attempt": attempt, "error": str(e), "trace": traceback.format_exc(), "timestamp": datetime.datetime.now().isoformat()})
                    time.sleep(2 ** attempt)
            if success:
                # Remove files on success
                try:
                    video_path.unlink(missing_ok=True)
                    task_path.unlink(missing_ok=True)
                except Exception:
                    pass
            else:
                # After final failure, log error and leave files for manual retry
                log({**task, "status": "error", "timestamp": datetime.datetime.now().isoformat()})
        except Exception as e:
            log({"status": "error", "error": str(e), "trace": traceback.format_exc(), "timestamp": datetime.datetime.now().isoformat()})
    time.sleep(SLEEP_SEC) 