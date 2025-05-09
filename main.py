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
    QTabWidget,
    QAbstractItemView,
)
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from dotenv import load_dotenv

from uploader import UploadPostClient

# Optional: pre-fill API key from environment (won't block UI on missing env)
load_dotenv()
ENV_API_KEY = os.getenv("API_KEY", "")
ENV_PI_IP = os.getenv("PI_IP", "")
ENV_SSH_KEY = os.getenv("SSH_KEY", str(Path.home() / ".ssh/pi_upload"))

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

class LogsWorker(QThread):
    """Worker thread to fetch Pi logs via SSH."""
    logs_ready = pyqtSignal(list)

    def __init__(self, pi_ip: str, ssh_key: str):
        super().__init__()
        self.pi_ip = pi_ip
        # Expand ~ in SSH key path to full home directory
        self.ssh_key = str(Path(ssh_key).expanduser())

    def run(self):
        import subprocess, json
        cmd = f"ssh -i {self.ssh_key} pi@{self.pi_ip} journalctl -u upload-worker --since today --no-pager -o cat"
        try:
            text = subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL, universal_newlines=True)
        except subprocess.CalledProcessError:
            text = ""
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                row = {
                    "timestamp": data.get("timestamp", ""),
                    "video": data.get("video", ""),
                    "user": data.get("user", ""),
                    "status": data.get("status", ""),
                    "message": data.get("error", data.get("message", "")),
                }
            except Exception:
                row = {
                    "timestamp": "",
                    "video": "",
                    "user": "",
                    "status": "",
                    "message": line,
                }
            rows.append(row)
        self.logs_ready.emit(rows)

class SCPWorker(QThread):
    """Worker thread to perform non-blocking SCP with progress updates."""
    progress = pyqtSignal(int)
    finished = pyqtSignal(bool)

    def __init__(self, video_path: str, json_path: str, ssh_key: str, pi_ip: str):
        super().__init__()
        self.video_path = video_path
        self.json_path = json_path
        self.ssh_key = ssh_key
        self.pi_ip = pi_ip

    def run(self):
        import subprocess, shlex
        # Emit indeterminate until we parse a percentage
        self.progress.emit(-1)
        cmd = f"scp -v -C -i {shlex.quote(self.ssh_key)} {shlex.quote(self.video_path)} {shlex.quote(self.json_path)} pi@{self.pi_ip}:/home/pi/upload_queue/"
        p = subprocess.Popen(cmd, shell=True, stderr=subprocess.PIPE, universal_newlines=True)
        for line in p.stderr:
            if '%' in line:
                try:
                    perc = int(line.strip().split('%')[0])
                    self.progress.emit(perc)
                except:
                    continue
        p.wait()
        success = (p.returncode == 0)
        self.finished.emit(success)
        # cleanup JSON file locally
        try:
            import os
            os.remove(self.json_path)
        except Exception:
            pass

class UserPanel(QWidget):
    """Panel for a single Upload-Post user: single uploads and scheduling."""
    def __init__(self, api_key_edit: QLineEdit, pi_ip_edit: QLineEdit, ssh_key_edit: QLineEdit, parent=None):
        super().__init__(parent)
        self.api_key_edit = api_key_edit
        self.pi_ip_edit = pi_ip_edit
        self.ssh_key_edit = ssh_key_edit
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
        self.scheduler_table.setDragDropMode(QAbstractItemView.InternalMove)
        self.scheduler_table.setDragDropOverwriteMode(False)
        self.scheduler_table.setSelectionBehavior(QAbstractItemView.SelectRows)
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
        self.run_on_pi_checkbox = QCheckBox("Run on Pi")
        sched_layout.addWidget(self.run_on_pi_checkbox)
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
            # Check top-level success and per-platform results
            if not response.get("success"):
                self.status_lbl.setText("❌ Upload failed.")
                QMessageBox.critical(self, "Upload Failed", str(response))
            else:
                # Inspect individual platform results
                errors = []
                results = response.get("results", {})
                for plat, plat_data in results.items():
                    if not plat_data.get("success"):
                        err = plat_data.get("error", "Unknown platform error")
                        errors.append(f"{plat}: {err}")
                if errors:
                    self.status_lbl.setText("❌ Upload failed.")
                    QMessageBox.critical(self, "Upload Failed", "; ".join(errors))
                else:
                    self.status_lbl.setText("✅ Upload successful!")
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
        # If opted to run on Pi, send tasks via SCP and return
        if self.run_on_pi_checkbox.isChecked():
            for task in tasks:
                try:
                    self._send_to_pi(task["path"], task["caption"], username, task["scheduled_time"])
                    self.status_lbl.setText(f"Queued {task['path'].name} on Pi ✓")
                except Exception as e:
                    QMessageBox.critical(self, "Error sending to Pi", str(e))
            return
        # Start scheduler worker
        self.scheduler_worker = SchedulerWorker(tasks, api_key, username)
        self.scheduler_worker.update_status.connect(lambda msg: self.status_lbl.setText(msg))
        self.scheduler_worker.finished_all.connect(lambda: QMessageBox.information(self, "Done", "All scheduled uploads complete."))
        self.scheduler_worker.start()

    def _send_to_pi(self, video_path, caption, user, scheduled_time):
        from pathlib import Path
        import json
        # Prepare task JSON file
        task = {
            "video": Path(video_path).name,
            "caption": caption,
            "user": user,
            "scheduled_at": scheduled_time.isoformat()
        }
        tmp_json = Path(video_path).with_suffix(".task.json")
        tmp_json.write_text(json.dumps(task))
        pi_ip = self.pi_ip_edit.text().strip()
        # Expand ~ in SSH key path to full home directory
        ssh_key = str(Path(self.ssh_key_edit.text().strip()).expanduser())
        # Show and reset progress bar
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        # Start SCP in background thread
        self.scp_worker = SCPWorker(
            video_path=str(video_path),
            json_path=str(tmp_json),
            ssh_key=ssh_key,
            pi_ip=pi_ip,
        )
        # Connect progress to handle indeterminate and value updates
        def _on_scp_progress(val: int):
            if val < 0:
                # Indeterminate mode
                self.progress_bar.setRange(0, 0)
            else:
                # Ensure determinate mode
                if self.progress_bar.maximum() == 0:
                    self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(val)
        self.scp_worker.progress.connect(_on_scp_progress)
        def _on_scp_finished(success: bool):
            self.progress_bar.setVisible(False)
            from pathlib import Path as _P
            if success:
                self.status_lbl.setText(f"Queued {_P(video_path).name} on Pi ✓")
            else:
                QMessageBox.critical(self, "SCP Error", f"Failed to send {_P(video_path).name} to Pi.")
        self.scp_worker.finished.connect(_on_scp_finished)
        self.scp_worker.start()

class PiTestWorker(QThread):
    """Worker thread to test SSH connection to Pi."""
    test_result = pyqtSignal(bool, str)

    def __init__(self, pi_ip: str, ssh_key: str):
        super().__init__()
        self.pi_ip = pi_ip
        # Expand ~ in SSH key path
        self.ssh_key = str(Path(ssh_key).expanduser())

    def run(self):
        import subprocess, shlex, json
        from pathlib import Path
        from datetime import datetime
        # Build SSH command
        cmd = f"ssh -o BatchMode=yes -i {shlex.quote(self.ssh_key)} pi@{self.pi_ip} echo ok"
        success = False
        message = ""
        try:
            output = subprocess.check_output(
                cmd, shell=True, stderr=subprocess.STDOUT,
                universal_newlines=True, timeout=10
            )
            if output.strip() == "ok":
                success = True
                message = "Connection successful"
            else:
                message = f"Unexpected response: {output.strip()}"
        except subprocess.CalledProcessError as e:
            message = f"SSH error {e.returncode}: {e.output.strip()}"
        except subprocess.TimeoutExpired:
            message = "SSH timed out"
        except Exception as e:
            message = f"{type(e).__name__}: {str(e)}"
        # Log the test result to logs/pi_test.log
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "pi_ip": self.pi_ip,
            "ssh_key": self.ssh_key,
            "success": success,
            "message": message,
        }
        with (log_dir / "pi_test.log").open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(log_entry) + "\n")
        # Emit result for UI
        self.test_result.emit(success, message)

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
        # Prefill API key from environment
        self.api_key_edit.setText(ENV_API_KEY)
        top_row.addWidget(self.api_key_edit)
        add_user_btn = QPushButton("Add User")
        add_user_btn.clicked.connect(self._add_user_panel)
        top_row.addWidget(add_user_btn)
        remove_user_btn = QPushButton("Remove User")
        remove_user_btn.clicked.connect(self._remove_user_panel)
        top_row.addWidget(remove_user_btn)
        top_row.addWidget(QLabel("Pi IP"))
        self.pi_ip_edit = QLineEdit()
        self.pi_ip_edit.setText(ENV_PI_IP)
        top_row.addWidget(self.pi_ip_edit)
        top_row.addWidget(QLabel("SSH Key"))
        self.ssh_key_edit = QLineEdit()
        self.ssh_key_edit.setText(ENV_SSH_KEY)
        top_row.addWidget(self.ssh_key_edit)
        # Test Pi connection button
        self.test_pi_btn = QPushButton("Test Pi Connection")
        self.test_pi_btn.clicked.connect(self._test_pi_connection)
        top_row.addWidget(self.test_pi_btn)
        main_layout.addLayout(top_row)

        # Scroll area for multiple UserPanels wrapped in tabs
        self.panels_container = QWidget()
        self.panels_layout = QHBoxLayout(self.panels_container)
        scroll = QScrollArea()
        scroll.setWidget(self.panels_container)
        scroll.setWidgetResizable(True)

        # Tabs for Uploads and Pi Logs
        self.tabs = QTabWidget()
        # Uploads tab
        upload_tab = QWidget()
        upload_layout = QVBoxLayout(upload_tab)
        upload_layout.addWidget(scroll)
        self.tabs.addTab(upload_tab, "Uploads")

        # Pi Logs tab
        self.logs_tab = QWidget()
        logs_layout = QVBoxLayout(self.logs_tab)
        self.logs_refresh_btn = QPushButton("Refresh Logs")
        self.logs_refresh_btn.clicked.connect(self.refresh_logs)
        logs_layout.addWidget(self.logs_refresh_btn)
        self.logs_table = QTableWidget(0,5)
        self.logs_table.setHorizontalHeaderLabels(["Timestamp","Video","User","Status","Message"])
        self.logs_table.horizontalHeader().setStretchLastSection(True)
        logs_layout.addWidget(self.logs_table)
        self.tabs.addTab(self.logs_tab, "Pi Logs")

        main_layout.addWidget(self.tabs)

        # Load logs when Pi Logs tab is selected
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # Add initial panel
        self._add_user_panel()

    def _add_user_panel(self):
        panel = UserPanel(self.api_key_edit, self.pi_ip_edit, self.ssh_key_edit)
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

    def refresh_logs(self):
        pi_ip = self.pi_ip_edit.text().strip()
        ssh_key = self.ssh_key_edit.text().strip()
        self.logs_refresh_btn.setEnabled(False)
        self.logs_table.setRowCount(0)
        self.logs_worker = LogsWorker(pi_ip, ssh_key)
        self.logs_worker.logs_ready.connect(self._populate_logs_table)
        self.logs_worker.finished.connect(lambda: self.logs_refresh_btn.setEnabled(True))
        self.logs_worker.start()

    def _on_tab_changed(self, index):
        if self.tabs.tabText(index) == "Pi Logs":
            self.refresh_logs()

    def _populate_logs_table(self, rows):
        self.logs_table.setRowCount(len(rows))
        for row_idx, row in enumerate(rows):
            ts_item = QTableWidgetItem(row.get("timestamp", ""))
            video_item = QTableWidgetItem(row.get("video", ""))
            user_item = QTableWidgetItem(row.get("user", ""))
            status_item = QTableWidgetItem(row.get("status", ""))
            msg_item = QTableWidgetItem(row.get("message", ""))
            self.logs_table.setItem(row_idx, 0, ts_item)
            self.logs_table.setItem(row_idx, 1, video_item)
            self.logs_table.setItem(row_idx, 2, user_item)
            self.logs_table.setItem(row_idx, 3, status_item)
            self.logs_table.setItem(row_idx, 4, msg_item)
            # Color based on status
            if row.get("status", "") == "ok":
                color = Qt.green
            elif row.get("status", "") == "error":
                color = Qt.red
            else:
                color = None
            if color:
                for item in (ts_item, video_item, user_item, status_item, msg_item):
                    item.setBackground(color)

    def _test_pi_connection(self):
        pi_ip = self.pi_ip_edit.text().strip()
        ssh_key_raw = self.ssh_key_edit.text().strip()
        # Expand and validate SSH key path
        ssh_path = Path(ssh_key_raw).expanduser()
        if not ssh_path.exists():
            QMessageBox.warning(self, "Invalid SSH Key", f"SSH key not found at {ssh_path}")
            return
        self.test_pi_btn.setEnabled(False)
        # Start test worker
        self.pi_test_worker = PiTestWorker(pi_ip, str(ssh_path))
        self.pi_test_worker.test_result.connect(self._on_test_result)
        self.pi_test_worker.finished.connect(lambda: self.test_pi_btn.setEnabled(True))
        self.pi_test_worker.start()

    def _on_test_result(self, success: bool, message: str):
        if success:
            QMessageBox.information(self, "Pi Connection", message)
        else:
            QMessageBox.critical(self, "Pi Connection", f"Failed to connect: {message}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_()) 