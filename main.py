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
    QScrollArea,
    QFrame,
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

class UserPanel(QWidget):
    """Panel for a single Upload-Post user: single uploads and scheduling."""
    def __init__(self, api_key_edit: QLineEdit, parent=None):
        super().__init__(parent)
        self.api_key_edit = api_key_edit
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        # Username
        layout.addWidget(QLabel("Upload-Post Username"))
        self.user_edit = QLineEdit()
        layout.addWidget(self.user_edit)

        # File picker
        file_layout = QHBoxLayout()
        self.file_edit = QLineEdit()
        self.file_edit.setPlaceholderText("Select video file (.mp4/.mov)")
        file_layout.addWidget(self.file_edit)
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._browse_file)
        file_layout.addWidget(browse_btn)
        layout.addLayout(file_layout)

        # Caption
        self.caption_edit = QLineEdit()
        self.caption_edit.setPlaceholderText("Caption (≤50 words)")
        layout.addWidget(self.caption_edit)

        # Status & Progress
        self.status_lbl = QLabel("")
        layout.addWidget(self.status_lbl)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0,100)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # Upload button
        upload_btn = QPushButton("Upload to TikTok")
        upload_btn.clicked.connect(self._do_upload)
        layout.addWidget(upload_btn)

        # Scheduler toggle
        self.scheduler_checkbox = QCheckBox("Enable Scheduler")
        self.scheduler_checkbox.toggled.connect(self._toggle_scheduler_panel)
        layout.addWidget(self.scheduler_checkbox)

        # Scheduler panel
        self.scheduler_panel = QFrame()
        sched_layout = QVBoxLayout(self.scheduler_panel)
        self.select_folder_btn = QPushButton("Select Folder for Scheduler")
        self.select_folder_btn.clicked.connect(self._populate_scheduler_table)
        sched_layout.addWidget(self.select_folder_btn)
        self.scheduler_table = QTableWidget(0,4)
        self.scheduler_table.setHorizontalHeaderLabels(["Video","Caption","Time","Remaining"])
        sched_layout.addWidget(self.scheduler_table)
        self.schedule_status_lbl = QLabel("")
        sched_layout.addWidget(self.schedule_status_lbl)
        self.refresh_btn = QPushButton("Refresh Schedule")
        self.refresh_btn.clicked.connect(self._refresh_schedule_status)
        self.start_schedule_btn = QPushButton("Start Scheduling")
        self.start_schedule_btn.clicked.connect(self._start_scheduling)
        row = QHBoxLayout()
        row.addWidget(self.refresh_btn)
        row.addWidget(self.start_schedule_btn)
        sched_layout.addLayout(row)
        self.scheduler_panel.setVisible(False)
        layout.addWidget(self.scheduler_panel)

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

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Upload GOAT")
        self.resize(800,600)
        self.user_panels = []  # track panels for dynamic add/remove
        self._build_ui()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        # API Key and Add User controls
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Upload-Post API Key"))
        self.api_key_edit = QLineEdit()
        top_row.addWidget(self.api_key_edit)
        add_user_btn = QPushButton("Add User")
        add_user_btn.clicked.connect(self._add_user_panel)
        top_row.addWidget(add_user_btn)
        remove_user_btn = QPushButton("Remove User")
        remove_user_btn.clicked.connect(self._remove_user_panel)
        top_row.addWidget(remove_user_btn)
        main_layout.addLayout(top_row)

        # Scroll area for multiple UserPanels
        self.panels_container = QWidget()
        self.panels_layout = QHBoxLayout(self.panels_container)
        scroll = QScrollArea()
        scroll.setWidget(self.panels_container)
        scroll.setWidgetResizable(True)
        main_layout.addWidget(scroll)

        # Add initial panel
        self._add_user_panel()

    def _add_user_panel(self):
        panel = UserPanel(self.api_key_edit)
        self.user_panels.append(panel)
        self.panels_layout.addWidget(panel)
        panel.show()

    def _remove_user_panel(self):
        """Remove the most recently added user panel."""
        if not self.user_panels:
            return
        panel = self.user_panels.pop()
        self.panels_layout.removeWidget(panel)
        panel.setParent(None)
        panel.deleteLater()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_()) 