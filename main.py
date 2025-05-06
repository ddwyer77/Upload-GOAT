import sys
import os
import json
from pathlib import Path
from datetime import datetime, timedelta
import traceback  # for detailed error dialogs
from PyQt5.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QProgressBar,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QDateTimeEdit,
    QCheckBox,
)
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from dotenv import load_dotenv

from uploader import UploadPostClient

# Optional: pre-fill API key from environment (won't block UI on missing env)
load_dotenv()
ENV_API_KEY = os.getenv("API_KEY", "")

class SchedulerWorker(QThread):
    """Worker thread to perform scheduled uploads at specified datetimes."""
    update_status = pyqtSignal(str)
    finished_all = pyqtSignal()

    def __init__(self, tasks: list, api_key: str, username: str):
        super().__init__()
        self.tasks = tasks  # list of dicts: {path, caption, scheduled_datetime}
        self.api_key = api_key
        self.username = username

    def run(self):
        from datetime import datetime
        import time
        client = UploadPostClient(self.api_key)
        for task in self.tasks:
            path = task["path"]
            caption = task["caption"]
            scheduled = task["scheduled_time"]
            # compute wait time
            now = datetime.now()
            delay = (scheduled - now).total_seconds()
            if delay > 0:
                self.update_status.emit(f"Waiting {int(delay)}s before uploading {path.name}")
                time.sleep(delay)
            self.update_status.emit(f"Uploading {path.name}")
            try:
                resp = client.upload_video(video_path=path, caption=caption, user=self.username)
                if resp.get("success"):
                    self.update_status.emit(f"Uploaded {path.name}")
                else:
                    self.update_status.emit(f"Failed {path.name}: {resp}")
            except Exception as ex:
                self.update_status.emit(f"Error {path.name}: {ex}")
        self.finished_all.emit()

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TikTok Uploader")
        # Set initial window size; allow height to adjust automatically
        self.resize(450, 320)
        self.setMinimumWidth(450)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout()
        # App title and subtitle
        title_lbl = QLabel("Upload GOAT")
        title_lbl.setAlignment(Qt.AlignCenter)
        title_lbl.setStyleSheet("font-size: 18pt; font-weight: bold;")
        layout.addWidget(title_lbl)
        subtitle_lbl = QLabel("a tool by ClipmodeGo")
        subtitle_lbl.setAlignment(Qt.AlignCenter)
        subtitle_lbl.setStyleSheet("font-size: 10pt; color: gray;")
        layout.addWidget(subtitle_lbl)

        # Upload-Post API Key field
        api_label = QLabel("Upload-Post API Key")
        layout.addWidget(api_label)
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setPlaceholderText("Paste your API Key here")
        # Pre-fill from environment if available
        if ENV_API_KEY:
            self.api_key_edit.setText(ENV_API_KEY)
        layout.addWidget(self.api_key_edit)

        # Upload-Post Username (global)
        user_label = QLabel("Upload-Post Username")
        layout.addWidget(user_label)
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("Upload-Post Username")
        layout.addWidget(self.user_edit)

        # Container for single-upload UI
        self.upload_panel = QWidget()
        upload_layout = QVBoxLayout(self.upload_panel)
        # File picker
        file_layout = QHBoxLayout()
        self.file_edit = QLineEdit()
        self.file_edit.setPlaceholderText("Select video file (.mp4/.mov)")
        file_layout.addWidget(self.file_edit)
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._browse_file)
        file_layout.addWidget(browse_btn)
        upload_layout.addLayout(file_layout)

        # Caption field
        self.caption_edit = QLineEdit()
        self.caption_edit.setPlaceholderText("Caption (≤50 words)")
        upload_layout.addWidget(self.caption_edit)

        # Status label
        self.status_lbl = QLabel("")
        upload_layout.addWidget(self.status_lbl)

        # Progress bar (hidden until upload starts)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        upload_layout.addWidget(self.progress_bar)

        # Upload button
        self.upload_btn = QPushButton("Upload to TikTok")
        self.upload_btn.clicked.connect(self._do_upload)
        upload_layout.addWidget(self.upload_btn)
        # Add upload_panel to main layout
        layout.addWidget(self.upload_panel)

        # Scheduler toggle
        self.scheduler_checkbox = QCheckBox("Enable Scheduler")
        self.scheduler_checkbox.toggled.connect(self._toggle_scheduler_panel)
        layout.addWidget(self.scheduler_checkbox)

        # Scheduler panel (collapsed by default)
        self.scheduler_panel = QWidget()
        sched_layout = QVBoxLayout(self.scheduler_panel)
        # Folder selector for batch uploads
        self.select_folder_btn = QPushButton("Select Folder for Scheduler")
        self.select_folder_btn.clicked.connect(self._populate_scheduler_table)
        sched_layout.addWidget(self.select_folder_btn)
        # Table of scheduled uploads
        self.scheduler_table = QTableWidget(0, 4)
        self.scheduler_table.setHorizontalHeaderLabels(["Video File", "Caption", "Scheduled Time", "Time Remaining"])
        self.scheduler_table.horizontalHeader().setStretchLastSection(True)
        sched_layout.addWidget(self.scheduler_table)
        # Status label for scheduler
        self.schedule_status_lbl = QLabel("")
        sched_layout.addWidget(self.schedule_status_lbl)
        # Refresh and start buttons
        btn_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh Schedule")
        self.refresh_btn.clicked.connect(self._refresh_schedule_status)
        self.start_schedule_btn = QPushButton("Start Scheduling")
        self.start_schedule_btn.clicked.connect(self._start_scheduling)
        btn_row.addWidget(self.refresh_btn)
        btn_row.addWidget(self.start_schedule_btn)
        sched_layout.addLayout(btn_row)
        self.scheduler_panel.setVisible(False)
        layout.addWidget(self.scheduler_panel)

        self.setLayout(layout)

    def _browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select video file",
            "",
            "Video Files (*.mp4 *.mov);;All Files (*)",
        )
        if path:
            self.file_edit.setText(path)

    def _log_result(self, payload: dict):
        payload["timestamp"] = datetime.utcnow().isoformat()
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / "upload_log.json"
        with log_file.open("a", encoding="utf-8") as fp:
            json.dump(payload, fp)
            fp.write("\n")

    def _do_upload(self):
        # Read and validate API key from UI
        api_key = self.api_key_edit.text().strip()
        if not api_key:
            QMessageBox.warning(self, "Validation Error", "API Key is required.")
            return
        try:
            client = UploadPostClient(api_key)
        except Exception as e:
            QMessageBox.critical(self, "Invalid API Key", str(e))
            return

        # Validate other inputs
        file_path = Path(self.file_edit.text())
        caption = self.caption_edit.text().strip()
        user = self.user_edit.text().strip()

        if not file_path.exists():
            QMessageBox.warning(self, "Validation Error", "Please select a valid video file.")
            return
        if not caption:
            QMessageBox.warning(self, "Validation Error", "Caption cannot be empty.")
            return
        if not user:
            QMessageBox.warning(self, "Validation Error", "Username is required.")
            return

        # Perform upload
        # show and reset progress
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.status_lbl.setText("Uploading...")
        QApplication.processEvents()

        try:
            # upload with progress callback
            response = client.upload_video(
                video_path=file_path,
                caption=caption,
                user=user,
                platforms=None,  # default to TikTok
                progress_callback=self._update_progress,
            )
            if response.get("success"):
                self.status_lbl.setText("✅ Upload successful!")
            else:
                self.status_lbl.setText("❌ Upload failed.")
                QMessageBox.critical(self, "Upload Failed", str(response))
        except Exception as ex:
            # Show status and a detailed error dialog
            self.status_lbl.setText("❌ Error")
            err_msg = str(ex)
            tb = traceback.format_exc()
            dlg = QMessageBox(self)
            dlg.setWindowTitle("Error during upload")
            dlg.setIcon(QMessageBox.Critical)
            dlg.setText(err_msg)
            dlg.setDetailedText(tb)
            dlg.exec_()
            response = {"error": err_msg, "traceback": tb}
        finally:
            # hide progress bar after completion
            self.progress_bar.setVisible(False)
            self._log_result({
                "file": str(file_path),
                "caption": caption,
                "username": user,
                "response": response,
            })

    def _update_progress(self, bytes_read: int, total: int):
        """Update the progress bar with bytes_read/total."""
        percent = int(bytes_read / total * 100) if total else 0
        self.progress_bar.setValue(percent)
        QApplication.processEvents()

    def _toggle_scheduler_panel(self, checked: bool):
        # Show/hide scheduler panel and single-upload panel
        self.upload_panel.setVisible(not checked)
        self.scheduler_panel.setVisible(checked)
        self.adjustSize()

    def _populate_scheduler_table(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Video Folder")
        if not directory:
            return
        from pathlib import Path
        self.scheduler_folder = Path(directory)
        videos = sorted(self.scheduler_folder.glob("*.mp4")) + sorted(self.scheduler_folder.glob("*.mov"))
        if not videos:
            QMessageBox.warning(self, "No Videos", "No .mp4 or .mov files found in that folder.")
            return
        self.scheduler_table.setRowCount(len(videos))
        from datetime import datetime, timedelta
        for row, path in enumerate(videos):
            # Video file name
            file_item = QTableWidgetItem(path.name)
            file_item.setFlags(file_item.flags() & ~Qt.ItemIsEditable)
            self.scheduler_table.setItem(row, 0, file_item)
            # Caption input
            caption_edit = QLineEdit(self.scheduler_panel)
            self.scheduler_table.setCellWidget(row, 1, caption_edit)
            # Scheduled time picker
            dt_edit = QDateTimeEdit(datetime.now() + timedelta(minutes=60), self.scheduler_panel)
            dt_edit.setCalendarPopup(True)
            self.scheduler_table.setCellWidget(row, 2, dt_edit)
            # Time remaining placeholder
            time_item = QTableWidgetItem("")
            time_item.setFlags(time_item.flags() & ~Qt.ItemIsEditable)
            self.scheduler_table.setItem(row, 3, time_item)
        self.scheduler_table.horizontalHeader().setStretchLastSection(True)

    def _refresh_schedule_status(self):
        # Update the Time Remaining column based on scheduled time
        from datetime import datetime
        for row in range(self.scheduler_table.rowCount()):
            dt_edit = self.scheduler_table.cellWidget(row, 2)
            scheduled = dt_edit.dateTime().toPyDateTime()
            now = datetime.now()
            diff = scheduled - now
            if diff.total_seconds() > 0:
                secs = int(diff.total_seconds())
                hrs, rem = divmod(secs, 3600)
                mins, secs = divmod(rem, 60)
                text = f"{hrs}h {mins}m {secs}s"
            else:
                text = "Due"
            item = self.scheduler_table.item(row, 3)
            if item:
                item.setText(text)

    def _start_scheduling(self):
        # Gather tasks from the scheduler table
        tasks = []
        for row in range(self.scheduler_table.rowCount()):
            file_name = self.scheduler_table.item(row, 0).text()
            path = self.scheduler_folder / file_name
            caption = self.scheduler_table.cellWidget(row, 1).text().strip()
            scheduled = self.scheduler_table.cellWidget(row, 2).dateTime().toPyDateTime()
            tasks.append({"path": path, "caption": caption, "scheduled_time": scheduled})
        api_key = self.api_key_edit.text().strip()
        username = self.user_edit.text().strip()
        if not tasks or not api_key or not username:
            QMessageBox.warning(self, "Validation Error", "Complete all scheduler entries before starting.")
            return
        # Update schedule status label and refresh time remaining
        self._refresh_schedule_status()
        self.schedule_status_lbl.setText("Videos scheduled for upload")
        # Start scheduler worker
        self.scheduler_worker = SchedulerWorker(tasks, api_key, username)
        self.scheduler_worker.update_status.connect(lambda msg: self.status_lbl.setText(msg))
        self.scheduler_worker.finished_all.connect(lambda: QMessageBox.information(self, "Done", "All scheduled uploads complete."))
        self.scheduler_worker.start()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_()) 