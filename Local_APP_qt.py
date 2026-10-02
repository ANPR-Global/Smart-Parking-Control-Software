import json
import os
import socket
import subprocess
import sys
import threading
import time
from _thread import LockType
from dataclasses import dataclass

import cv2
import requests
from PySide6.QtCore import QPoint, QRect, QTimer, Qt
from PySide6.QtGui import QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QDoubleSpinBox,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

SUPABASE_REST_URL = os.getenv("SUPABASE_REST_URL", "https://ftudsdivlhqifvmclpis.supabase.co")
SUPABASE_ANON_KEY = os.getenv(
    "SUPABASE_ANON_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZ0dWRzZGl2bGhxaWZ2bWNscGlzIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODczMDU5MzcsImV4cCI6MjEwMjg4MTkzN30.owX-iw8sT4n5PtgjuDG6-N05hCOka7pqfQSqpHPzSJk",
)
CLIENT_SESSION_TOKEN = os.getenv("CLIENT_SESSION_TOKEN", "")
PARKING_REFRESH_INTERVAL_MS = 60000
FRAME_INTERVAL_MS = 33
LIVE_WALL_TARGET_FPS = 8
LIVE_WALL_PREVIEW_WIDTH = 960
WORKER_URL = os.getenv("WORKER_URL", "https://parking.wyom.in/ocr")
PARKING_API_KEY = os.getenv("PARKING_API_KEY", "")


def worker_base_url(url=WORKER_URL):
    base = (url or "").strip().rstrip("/")
    if base.endswith("/ocr"):
        base = base[:-4]
    return base.rstrip("/")

ENDPOINT_CONFIG_PATH = os.getenv(
    "ENDPOINT_CONFIG_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "parking_endpoints.json"),
)
DEVICE_CONFIG_PATH = os.getenv(
    "DEVICE_CONFIG_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "device_config.json"),
)
GATE_ASSIGNMENTS_PATH = os.getenv(
    "GATE_ASSIGNMENTS_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "gate_assignments.json"),
)
WORKER_CONFIG_PATH = os.getenv(
    "WORKER_CONFIG_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "worker_config.json"),
)
CPP_DETECTOR_BIN = os.getenv(
    "CPP_DETECTOR_BIN",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "cpp", "gate_detector_worker.exe"),
)
OPENCV_BIN_DIR = os.getenv(
    "OPENCV_BIN_DIR",
    "C:/msys64/mingw64/bin",
)
MSYS2_MINGW64_BIN = os.getenv("MSYS2_MINGW64_BIN", "C:/msys64/mingw64/bin")

VIDEO_EXTENSIONS = (".mp4", ".avi", ".mov", ".mkv", ".wmv")
LAN_SCAN_PORTS = (80, 554, 8080, 8554, 8899)
MAX_LOG_LINES = 300
DEFAULT_DETECTION_PARAMS = {
    "line_ratio": 0.10,
    "vehicle_detection_interval": 10,
    "vehicle_exit_frames": 20,
    "vehicle_stationary_frames": 50,
    "vehicle_roi_percent": 0.50,
    "plate_detection_interval": 10,
    "plate_dwell_frames": 10,
    "track_iou_threshold": 0.15,
    "vehicle_confidence": 0.35,
}

VEHICLE_MODEL_PATH = os.getenv(
    "VEHICLE_MODEL_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "yolo11n.onnx"),
)
PLATE_MODEL_PATH = os.getenv(
    "PLATE_MODEL_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "yolo-v9-t-384-license-plates-end2end.onnx"),
)


class EndpointRegistry:
    def __init__(self, path):
        self.path = path
        self.endpoints = []
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as config_file:
                data = json.load(config_file)
            self.endpoints = data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            self.endpoints = []

    def save(self):
        with open(self.path, "w", encoding="utf-8") as config_file:
            json.dump(self.endpoints, config_file, indent=2)

    def add(self, kind, name, url, source="manual"):
        endpoint = {
            "id": f"{kind}:{url}",
            "kind": kind,
            "name": name or url,
            "url": url,
            "source": source,
        }
        self.endpoints = [item for item in self.endpoints if item.get("id") != endpoint["id"]]
        self.endpoints.append(endpoint)
        self.save()
        return endpoint

    def discover(self):
        discovered = []
        for index in range(5):
            capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
            if capture.isOpened():
                endpoint = self.add("camera", f"Device camera {index}", str(index), "device")
                discovered.append(endpoint)
            capture.release()

        try:
            local_ip = socket.gethostbyname(socket.gethostname())
            prefix = ".".join(local_ip.split(".")[:-1])
        except Exception:
            return discovered

        for host_number in range(1, 255):
            host = f"{prefix}.{host_number}"
            for port in LAN_SCAN_PORTS:
                connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                connection.settimeout(0.08)
                try:
                    if connection.connect_ex((host, port)) != 0:
                        continue
                    kind = "camera" if port in (554, 8554) else "barrier"
                    scheme = "rtsp" if kind == "camera" else "http"
                    url = f"{scheme}://{host}:{port}"
                    endpoint = self.add(kind, f"LAN {kind} {host}:{port}", url, "lan")
                    discovered.append(endpoint)
                finally:
                    connection.close()
        return discovered

    def by_kind(self, kind):
        return [item for item in self.endpoints if item.get("kind") == kind]


class GateAssignments:
    def __init__(self, path):
        self.path = path
        self.data = {}
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as config_file:
                data = json.load(config_file)
            self.data = data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            self.data = {}

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as config_file:
            json.dump(self.data, config_file, indent=2)

    @staticmethod
    def gate_key(gate_name, gate_type):
        return f"{gate_type}:{gate_name}"

    def get(self, parking_id, gate_name, gate_type):
        parking = self.data.get(parking_id, {})
        assignment = parking.get(self.gate_key(gate_name, gate_type))
        if assignment:
            return assignment
        return parking.get(gate_name)

    def set(
        self,
        parking_id,
        gate_name,
        gate_type,
        camera_url,
        barrier_url,
        detection_area=None,
        entry_side=None,
        media_path=None,
        media_input_type=None,
        detection_params=None,
    ):
        parking = self.data.setdefault(parking_id, {})
        key = self.gate_key(gate_name, gate_type)
        current = parking.get(key, {})
        parking[key] = {
            "camera_url": camera_url,
            "barrier_url": barrier_url,
            "detection_area": current.get("detection_area") if detection_area is None else detection_area,
            "entry_side": current.get("entry_side", "bottom") if entry_side is None else entry_side,
            "media_path": current.get("media_path") if media_path is None else media_path,
            "media_input_type": current.get("media_input_type") if media_input_type is None else media_input_type,
            "detection_params": current.get("detection_params", DEFAULT_DETECTION_PARAMS.copy()) if detection_params is None else detection_params,
        }
        self.save()


@dataclass
class GateCardState:
    key: str
    parking_id: str
    gate_name: str
    gate_type: str
    mode: str
    card: QFrame
    toggle_button: QPushButton
    content: QWidget
    camera_combo: QComboBox
    barrier_combo: QComboBox | None
    entry_side_combo: QComboBox | None
    detection_summary_label: QLabel | None
    detection_controls: dict | None
    video_label: QLabel
    worker_status_label: QLabel
    timer: QTimer | None = None
    capture: cv2.VideoCapture | None = None
    shared_source: str | None = None
    media_path: str | None = None
    media_input_type: str | None = None
    last_paint_ts: float = 0.0
    expanded: bool = False
    latest_frame: any = None
    worker_state_path: str | None = None


@dataclass
class SharedStreamState:
    source: str
    capture: cv2.VideoCapture
    timer: QTimer
    subscribers: set
    latest_frame: any = None
    frame_lock: LockType | None = None
    reader_thread: threading.Thread | None = None
    running: bool = False


class CppDetectionSupervisor:
    def __init__(self, binary_path):
        self.binary_path = binary_path
        self.workers = {}
        self.preview_cache = {}
        self.missing_binary_logged = False
        self.logs_dir = os.path.join(os.path.dirname(binary_path or "."), "logs")
        session_id = f"{os.getpid()}-{time.time_ns()}"
        self.state_dir = os.path.join(os.path.dirname(binary_path or "."), "state", session_id)
        os.makedirs(self.logs_dir, exist_ok=True)
        os.makedirs(self.state_dir, exist_ok=True)

    @staticmethod
    def safe_gate_name(gate_id):
        return gate_id.replace("|", "_").replace(":", "_")

    def state_path(self, gate_id):
        return os.path.join(os.path.dirname(self.binary_path or "."), "state", "controller", self.safe_gate_name(gate_id) + ".json")

    def preview_path(self, gate_id):
        return os.path.join(os.path.dirname(self.binary_path or "."), "state", "controller", self.safe_gate_name(gate_id) + ".jpg")

    def _can_launch(self):
        return bool(self.binary_path) and os.path.isfile(self.binary_path)

    def _worker_env(self):
        env = os.environ.copy()
        path_parts = [env.get("PATH", "")]
        binary_dir = os.path.dirname(self.binary_path) if self.binary_path else ""
        for candidate in (binary_dir, OPENCV_BIN_DIR, MSYS2_MINGW64_BIN):
            if candidate and os.path.isdir(candidate):
                path_parts.insert(0, candidate)
        env["PATH"] = os.pathsep.join(path_parts)
        return env

    def ensure_runtime_dlls(self):
        if not self.binary_path:
            return []
        binary_dir = os.path.dirname(self.binary_path)
        if not binary_dir or not os.path.isdir(binary_dir):
            return []

        staged = []

        # Prefer already-staged OpenCV binaries that match the current worker build.
        opencv_runtime_dlls = [
            name
            for name in os.listdir(binary_dir)
            if name.lower().startswith("libopencv_") and name.lower().endswith(".dll")
        ]
        if not opencv_runtime_dlls and OPENCV_BIN_DIR and os.path.isdir(OPENCV_BIN_DIR):
            opencv_runtime_dlls = [
                name
                for name in os.listdir(OPENCV_BIN_DIR)
                if name.lower().startswith("libopencv_") and name.lower().endswith(".dll")
            ]
        opencv_runtime_dlls.sort()

        mingw_runtime_dlls = [
            "libstdc++-6.dll",
            "libgcc_s_seh-1.dll",
            "libwinpthread-1.dll",
            "libgomp-1.dll",
            "libssp-0.dll",
        ]

        copy_plan = []
        for dll_name in opencv_runtime_dlls:
            copy_plan.append((OPENCV_BIN_DIR, dll_name))
        for dll_name in mingw_runtime_dlls:
            copy_plan.append((MSYS2_MINGW64_BIN, dll_name))

        for source_dir, dll_name in copy_plan:
            if not source_dir or not os.path.isdir(source_dir):
                continue
            source_path = os.path.join(source_dir, dll_name)
            if not os.path.isfile(source_path):
                continue

            destination = os.path.join(binary_dir, dll_name)
            if os.path.isfile(destination):
                continue
            try:
                with open(source_path, "rb") as src_handle, open(destination, "wb") as dst_handle:
                    dst_handle.write(src_handle.read())
                staged.append(destination)
            except OSError:
                continue
        return staged

    @staticmethod
    def _config_signature(config):
        return json.dumps(
            config,
            sort_keys=True,
        )

    def sync_gate(self, gate_id, config):
        input_path = config.get("input_path")
        input_mode = config.get("input_mode")
        entry_side = config.get("entry_side")
        detection_area = config.get("detection_area")
        if not input_path or not detection_area or not entry_side:
            self.stop_gate(gate_id)
            return {"status": "stopped", "gate_id": gate_id}

        if not self._can_launch():
            self.stop_gate(gate_id)
            return {"status": "missing_binary", "gate_id": gate_id}

        staged_dlls = self.ensure_runtime_dlls()

        signature = self._config_signature({"config_path": WORKER_CONFIG_PATH})
        existing = self.workers.get(gate_id)
        controller = self.workers.get("__controller__")
        if controller:
            process = controller.get("process")
            if process and process.poll() is None:
                self.workers[gate_id] = controller
                return {"status": "running", "gate_id": gate_id, "pid": process.pid, "log_path": controller.get("log_path")}
        if existing and existing.get("signature") == signature:
            process = existing.get("process")
            if process and process.poll() is None:
                return {
                    "status": "running",
                    "gate_id": gate_id,
                    "pid": process.pid,
                    "log_path": existing.get("log_path"),
                }

        self.stop_gate(gate_id)

        state_path = self.state_path(gate_id)
        preview_path = self.preview_path(gate_id)
        try:
            os.remove(state_path)
        except FileNotFoundError:
            pass
        try:
            os.remove(preview_path)
        except FileNotFoundError:
            pass

        command = [
            self.binary_path,
            "--config-path",
            WORKER_CONFIG_PATH,
        ]
        optional_args = {
        }
        for key, arg_name in optional_args.items():
            value = (config.get(key) or "").strip() if isinstance(config.get(key), str) else config.get(key)
            if value:
                command.extend([arg_name, str(value)])

        log_file_name = self.safe_gate_name(gate_id) + ".log"
        log_path = os.path.join(self.logs_dir, log_file_name)
        log_handle = open(log_path, "ab")
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            command,
            stdout=log_handle,
            stderr=log_handle,
            creationflags=creation_flags,
            env=self._worker_env(),
        )
        controller_info = {
            "process": process,
            "signature": signature,
            "log_path": log_path,
            "log_handle": log_handle,
        }
        self.workers["__controller__"] = controller_info
        self.workers[gate_id] = controller_info
        return {
            "status": "started",
            "gate_id": gate_id,
            "pid": process.pid,
            "log_path": log_path,
            "staged_dlls": staged_dlls,
        }

    def stop_gate(self, gate_id):
        cached = self.preview_cache.pop(gate_id, None)
        if cached and cached[3]:
            try:
                os.remove(cached[3])
            except FileNotFoundError:
                pass
        existing = self.workers.pop(gate_id, None)
        if not existing:
            return
        if any(info is existing for key, info in self.workers.items() if key != "__controller__"):
            return
        self.workers.pop("__controller__", None)
        process = existing.get("process")
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.5)
            except Exception:
                process.kill()
        handle = existing.get("log_handle")
        if handle is not None:
            handle.close()
        try:
            os.remove(self.state_path(gate_id))
        except FileNotFoundError:
            pass
        try:
            os.remove(self.preview_path(gate_id))
        except FileNotFoundError:
            pass

    def read_state(self, gate_id):
        try:
            with open(self.state_path(gate_id), "r", encoding="utf-8") as state_file:
                data = json.load(state_file)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def read_preview(self, gate_id):
        state = self.read_state(gate_id)
        preview_path = state.get("preview_path")
        if not preview_path:
            cached = self.preview_cache.get(gate_id)
            return (cached[1].copy(), cached[2]) if cached else (None, state)
        frame_number = state.get("frame")
        cached = self.preview_cache.get(gate_id)
        if cached and cached[0] == frame_number:
            return cached[1].copy(), cached[2]
        try:
            with open(preview_path, "rb") as preview_file:
                encoded = preview_file.read()
            if not encoded:
                return (cached[1].copy(), cached[2]) if cached else (None, state)
            import numpy as np
            frame = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is not None:
                previous_path = cached[3] if cached else None
                self.preview_cache[gate_id] = (frame_number, frame.copy(), state, preview_path)
                if previous_path and previous_path != preview_path:
                    try:
                        os.remove(previous_path)
                    except FileNotFoundError:
                        pass
                return frame, state
            return (cached[1].copy(), cached[2]) if cached else (None, state)
        except OSError:
            return (cached[1].copy(), cached[2]) if cached else (None, state)

    def collect_exited_workers(self):
        events = []
        for gate_id, info in list(self.workers.items()):
            process = info.get("process")
            if process is None:
                continue
            code = process.poll()
            if code is None:
                continue
            handle = info.get("log_handle")
            if handle is not None:
                handle.close()
            self.workers.pop(gate_id, None)
            events.append(
                {
                    "gate_id": gate_id,
                    "exit_code": code,
                    "log_path": info.get("log_path"),
                    "signature": info.get("signature"),
                }
            )
        return events

    def stop_all(self):
        controller = self.workers.get("__controller__")
        self.workers.clear()
        process = controller.get("process") if controller else None
        if process and process.poll() is None:
            process.terminate()
        handle = controller.get("log_handle") if controller else None
        if handle is not None:
            handle.close()

    def is_running(self, gate_id):
        info = self.workers.get("__controller__") or self.workers.get(gate_id)
        process = info.get("process") if info else None
        return bool(process and process.poll() is None)


class RoiCanvas(QLabel):
    def __init__(self, pixmap, line_ratio=0.10):
        super().__init__()
        self.setPixmap(pixmap)
        self.setFixedSize(pixmap.size())
        self._dragging = False
        self._start = QPoint()
        self._end = QPoint()
        self._line_ratio = line_ratio

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging = True
        self._start = event.position().toPoint()
        self._end = self._start
        self.update()

    def mouseMoveEvent(self, event):
        if not self._dragging:
            return
        self._end = event.position().toPoint()
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging = False
        self._end = event.position().toPoint()
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        rect = self.selected_rect_pixels()
        if rect is None:
            return
        painter = QPainter(self)
        pen = QPen(Qt.GlobalColor.cyan)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawRect(rect)
        top_y = rect.top() + round(rect.height() * self._line_ratio)
        bottom_y = rect.bottom() - round(rect.height() * self._line_ratio)
        painter.setPen(QPen(Qt.GlobalColor.yellow, 2))
        painter.drawLine(rect.left(), top_y, rect.right(), top_y)
        painter.setPen(QPen(Qt.GlobalColor.magenta, 2))
        painter.drawLine(rect.left(), bottom_y, rect.right(), bottom_y)

    def selected_rect_pixels(self):
        if self._start == self._end:
            return None
        raw = QRect(self._start, self._end).normalized()
        clipped = raw.intersected(self.rect())
        if clipped.width() < 4 or clipped.height() < 4:
            return None
        return clipped


class DetectionRoiDialog(QDialog):
    def __init__(self, frame_bgr, gate_name, line_ratio=0.10, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Draw Detection Area - {gate_name}")
        self.setModal(True)

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        bytes_per_line = ch * w
        image = QImage(rgb.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(image)

        max_w = 1200
        max_h = 760
        if pixmap.width() > max_w or pixmap.height() > max_h:
            pixmap = pixmap.scaled(
                max_w,
                max_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Drag ROI. Yellow is TOP and magenta is BOTTOM."))

        self.canvas = RoiCanvas(pixmap, line_ratio)
        layout.addWidget(self.canvas, alignment=Qt.AlignmentFlag.AlignCenter)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_normalized_rect(self):
        rect = self.canvas.selected_rect_pixels()
        if rect is None:
            return None
        width = max(1, self.canvas.width())
        height = max(1, self.canvas.height())
        return {
            "x": rect.x() / width,
            "y": rect.y() / height,
            "w": rect.width() / width,
            "h": rect.height() / height,
        }


class ParkingQtApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Parking Gate Client (Qt) - Phase 1+")
        self.resize(1440, 940)

        self.http = requests.Session()
        self.endpoint_registry = EndpointRegistry(ENDPOINT_CONFIG_PATH)
        self.gate_assignments = GateAssignments(GATE_ASSIGNMENTS_PATH)
        self.device_config = self.load_device_config()

        self.remote_parkings = []
        self.locked_parking_ids = []
        self.locked_gate_signature = None

        self.settings_gate_states = {}
        self.live_gate_states = {}
        self.shared_streams = {}
        self.active_media_gate_ids = set()
        self.cpp_detection = CppDetectionSupervisor(CPP_DETECTOR_BIN)
        self.worker_status_cache = {}
        self.worker_failed_loader_signatures = {}
        self.temporary_media_workers = {}
        self._build_ui()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.poll_remote_parking_changes)

        self.worker_status_timer = QTimer(self)
        self.worker_status_timer.timeout.connect(self.poll_worker_health)
        self.worker_status_timer.start(2000)

        self.reload_remote_parkings(initial_load=True)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)

        self.tabs = QTabWidget()
        root_layout.addWidget(self.tabs, stretch=1)

        self.settings_tab = QWidget()
        self.settings_tab_layout = QVBoxLayout(self.settings_tab)
        self.tabs.addTab(self.settings_tab, "Settings")

        self.live_tab = QWidget()
        self.live_tab_layout = QVBoxLayout(self.live_tab)
        self.tabs.addTab(self.live_tab, "Live Camera")

        self._build_settings_tab()
        self._build_live_tab()

        self.log_console = QPlainTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setMaximumBlockCount(MAX_LOG_LINES)
        self.log_console.setPlaceholderText("Runtime logs...")
        self.log_console.setFixedHeight(170)
        self.log_console.setStyleSheet("padding:8px; border:1px solid #444; background:#111; color:#9eff9e;")
        root_layout.addWidget(self.log_console)
        self.log_message("Ready")

    def _build_settings_tab(self):
        controls = QWidget()
        controls_layout = QVBoxLayout(controls)

        top_row = QHBoxLayout()
        top_row.setAlignment(Qt.AlignmentFlag.AlignTop)
        parking_box = QVBoxLayout()
        parking_box.addWidget(QLabel("Parking (check one or more)"))
        self.parking_list = QListWidget()
        self.parking_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.parking_list.setMinimumWidth(460)
        self.parking_list.setMaximumHeight(102)
        self.parking_list.setStyleSheet("QListWidget{background:#1e2226; color:#f2f2f2; border:1px solid #444;}")
        parking_box.addWidget(self.parking_list)
        top_row.addLayout(parking_box, stretch=1)

        self.lock_button = QPushButton("Lock Parking")
        self.lock_button.clicked.connect(self.lock_parking)
        top_row.addWidget(self.lock_button)

        top_row.addWidget(QLabel("Client Session Token"))
        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_edit.setText(self.device_config.get("client_session_token") or CLIENT_SESSION_TOKEN)
        top_row.addWidget(self.token_edit, stretch=2)

        save_token_btn = QPushButton("Save Token")
        save_token_btn.clicked.connect(self.save_session_token)
        top_row.addWidget(save_token_btn)

        top_row.addWidget(QLabel("Gate API Key"))
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setText(self.device_config.get("parking_api_key") or PARKING_API_KEY)
        top_row.addWidget(self.api_key_edit, stretch=2)
        save_api_key_btn = QPushButton("Save API Key")
        save_api_key_btn.clicked.connect(self.save_api_key)
        top_row.addWidget(save_api_key_btn)

        controls_layout.addLayout(top_row)

        action_row = QHBoxLayout()
        discover_btn = QPushButton("Discover LAN / Device")
        discover_btn.clicked.connect(self.discover_endpoints)
        action_row.addWidget(discover_btn)

        add_manual_btn = QPushButton("Add Manual Endpoint")
        add_manual_btn.clicked.connect(self.add_manual_endpoint)
        action_row.addWidget(add_manual_btn)

        refresh_btn = QPushButton("Refresh Gate Grid")
        refresh_btn.clicked.connect(self.render_gate_grids)
        action_row.addWidget(refresh_btn)

        collapse_all_btn = QPushButton("Collapse All")
        collapse_all_btn.clicked.connect(lambda: self.set_all_expanded(self.settings_gate_states, False))
        action_row.addWidget(collapse_all_btn)
        action_row.addStretch()

        controls_layout.addLayout(action_row)
        self.settings_tab_layout.addWidget(controls)

        self.settings_scroll = QScrollArea()
        self.settings_scroll.setWidgetResizable(True)
        self.settings_grid_container = QWidget()
        self.settings_grid = QGridLayout(self.settings_grid_container)
        self.settings_grid.setContentsMargins(8, 8, 8, 8)
        self.settings_grid.setSpacing(10)
        self.settings_scroll.setWidget(self.settings_grid_container)
        self.settings_tab_layout.addWidget(self.settings_scroll, stretch=1)

    def _build_live_tab(self):
        live_header = QHBoxLayout()
        live_header.addWidget(QLabel("Live view is memory-light: only expanded gates keep active streams."))

        live_header.addWidget(QLabel("Wall FPS"))
        self.wall_fps_combo = QComboBox()
        self.wall_fps_combo.addItems(["6", "8", "10", "12", "16", "20", "24"])
        self.wall_fps_combo.setCurrentText(str(LIVE_WALL_TARGET_FPS))
        self.wall_fps_combo.currentTextChanged.connect(self.on_wall_profile_changed)
        live_header.addWidget(self.wall_fps_combo)

        refresh_btn = QPushButton("Refresh Live Grid")
        refresh_btn.clicked.connect(self.render_gate_grids)
        live_header.addWidget(refresh_btn)

        collapse_all_btn = QPushButton("Collapse All")
        collapse_all_btn.clicked.connect(lambda: self.set_all_expanded(self.live_gate_states, False))
        live_header.addWidget(collapse_all_btn)
        live_header.addStretch()

        self.live_tab_layout.addLayout(live_header)

        self.live_scroll = QScrollArea()
        self.live_scroll.setWidgetResizable(True)
        self.live_grid_container = QWidget()
        self.live_grid = QGridLayout(self.live_grid_container)
        self.live_grid.setContentsMargins(8, 8, 8, 8)
        self.live_grid.setSpacing(10)
        self.live_scroll.setWidget(self.live_grid_container)
        self.live_tab_layout.addWidget(self.live_scroll, stretch=1)

    def on_wall_profile_changed(self):
        # Existing stream timers keep running; render throttle takes effect immediately.
        self.log_message("Live wall FPS profile updated.")

    def load_device_config(self):
        try:
            with open(DEVICE_CONFIG_PATH, "r", encoding="utf-8") as config_file:
                data = json.load(config_file)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def save_device_config(self):
        temporary = DEVICE_CONFIG_PATH + ".tmp"
        with open(temporary, "w", encoding="utf-8") as config_file:
            json.dump(self.device_config, config_file, indent=2)
        os.replace(temporary, DEVICE_CONFIG_PATH)

    def log_message(self, message):
        timestamp = time.strftime("%H:%M:%S")
        self.log_console.appendPlainText(f"[{timestamp}] {message}")
        scroll = self.log_console.verticalScrollBar()
        scroll.setValue(scroll.maximum())

    def frame_to_pixmap(self, frame):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        bytes_per_line = ch * w
        image = QImage(rgb.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(image)

    def poll_worker_health(self):
        for event in self.cpp_detection.collect_exited_workers():
            gate_id = event.get("gate_id", "unknown")
            exit_code = event.get("exit_code")
            log_path = event.get("log_path")
            signature = event.get("signature")
            self.worker_status_cache.pop(gate_id, None)
            self.log_message(
                f"Worker exited for {gate_id} with code {exit_code}. Logs: {log_path}"
            )
            if exit_code in (3221225785, -1073741511):
                if signature:
                    self.worker_failed_loader_signatures[gate_id] = signature
                self.log_message(
                    "Worker failed before startup (Windows loader entrypoint issue). "
                    "Re-check OpenCV/MinGW runtime DLL compatibility."
                )
        self.update_all_worker_statuses()
        self.poll_temporary_media_workers()

    def poll_temporary_media_workers(self):
        for worker_id, info in list(self.temporary_media_workers.items()):
            process = info["process"]
            if process.poll() is None:
                continue
            info["log_handle"].close()
            self.temporary_media_workers.pop(worker_id, None)
            state = info["state"]
            if state.timer is not None:
                self.render_temporary_media_preview(state, worker_id, info)
                state.timer.stop()
                state.timer.deleteLater()
                state.timer = None
            state.worker_state_path = None
            for path in (info["state_path"], info["preview_path"], info["config_path"]):
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
            if process.returncode == 0:
                self.log_message(f"Temporary {info['media_type']} OCR pipeline completed for {state.gate_name}.")
            else:
                self.log_message(f"Temporary media worker failed for {state.gate_name} with code {process.returncode}. Logs: {info['log_path']}")
            QTimer.singleShot(0, lambda s=state: self.restore_permanent_gate_preview(s))

    def start_temporary_media_worker(self, state, file_path, media_type, detection_area):
        gate_id = self.normalized_gate_id(state.parking_id, state.gate_type, state.gate_name)
        self.stop_temporary_media_worker_for_gate(gate_id)
        worker_id = f"media-{time.time_ns()}-{gate_id}"
        safe_id = self.cpp_detection.safe_gate_name(worker_id)
        temp_dir = os.path.join(os.path.dirname(CPP_DETECTOR_BIN), "state", "temporary")
        os.makedirs(temp_dir, exist_ok=True)
        state_path = os.path.join(temp_dir, safe_id + ".json")
        preview_path = os.path.join(temp_dir, safe_id + ".jpg")
        config_path = os.path.join(temp_dir, safe_id + ".config.json")
        assignment = self.gate_assignments.get(state.parking_id, state.gate_name, state.gate_type) or {}
        params = self.normalize_detection_params(assignment.get("detection_params"))
        temporary_config = {
            "version": 1,
            "parking_id": state.parking_id,
            "session_token": self.get_client_session_token(),
            "api_key": self.api_key_edit.text().strip(),
            "worker_url": WORKER_URL,
            "vehicle_model_path": VEHICLE_MODEL_PATH,
            "plate_model_path": PLATE_MODEL_PATH,
            "temporary_mode": True,
            "gates": [{
                "gate_id": gate_id,
                "gate_name": state.gate_name,
                "gate_type": state.gate_type,
                "camera_url": assignment.get("camera_url", ""),
                "barrier_url": assignment.get("barrier_url", ""),
                "media_path": file_path,
                "media_input_type": media_type,
                "entry_side": assignment.get("entry_side", "bottom"),
                "detection_area": detection_area,
                "detection_params": params,
            }],
        }
        with open(config_path + ".tmp", "w", encoding="utf-8") as config_file:
            json.dump(temporary_config, config_file, indent=2)
        os.replace(config_path + ".tmp", config_path)
        command = [
            os.path.join(os.path.dirname(CPP_DETECTOR_BIN), "gate_detector_core.exe"),
            "--config-path", config_path,
            "--gate-id", gate_id,
            "--state-path", state_path,
            "--preview-path", preview_path,
        ]
        log_path = os.path.join(self.cpp_detection.logs_dir, safe_id + ".log")
        log_handle = open(log_path, "ab")
        process = subprocess.Popen(
            command,
            stdout=log_handle,
            stderr=log_handle,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=self.cpp_detection._worker_env(),
        )
        state.worker_state_path = state_path
        self.temporary_media_workers[worker_id] = {
            "process": process,
            "log_handle": log_handle,
            "log_path": log_path,
            "state": state,
            "media_type": media_type,
            "state_path": state_path,
            "preview_path": preview_path,
            "config_path": config_path,
            "detection_area": detection_area,
        }
        if state.timer is not None:
            state.timer.stop()
            state.timer.deleteLater()
        state.timer = QTimer(self)
        state.timer.timeout.connect(lambda s=state, wid=worker_id: self.render_temporary_media_preview(s, wid))
        state.timer.start(FRAME_INTERVAL_MS)
        self.log_message(f"Temporary {media_type} worker started for {state.gate_name}.")

    def stop_temporary_media_worker_for_gate(self, gate_id):
        for worker_id, info in list(self.temporary_media_workers.items()):
            state = info["state"]
            state_gate_id = self.normalized_gate_id(state.parking_id, state.gate_type, state.gate_name)
            if state_gate_id != gate_id:
                continue
            process = info["process"]
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except Exception:
                    process.kill()
            info["log_handle"].close()
            if state.timer is not None:
                state.timer.stop()
                state.timer.deleteLater()
                state.timer = None
            state.worker_state_path = None
            self.temporary_media_workers.pop(worker_id, None)
            for path in (info["state_path"], info["preview_path"], info["config_path"]):
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
            QTimer.singleShot(0, lambda s=state: self.restore_permanent_gate_preview(s))

    def stop_all_temporary_media_workers(self):
        gate_ids = {
            self.normalized_gate_id(info["state"].parking_id, info["state"].gate_type, info["state"].gate_name)
            for info in self.temporary_media_workers.values()
        }
        for gate_id in gate_ids:
            self.stop_temporary_media_worker_for_gate(gate_id)

    def render_temporary_media_preview(self, state, worker_id, completed_info=None):
        info = completed_info or self.temporary_media_workers.get(worker_id)
        if not info:
            return
        try:
            with open(info["state_path"], "r", encoding="utf-8") as state_file:
                worker_state = json.load(state_file)
            preview_path = worker_state.get("preview_path")
            if not preview_path:
                return
            import numpy as np
            with open(preview_path, "rb") as preview_file:
                frame = cv2.imdecode(np.frombuffer(preview_file.read(), dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                return
            state.latest_frame = frame.copy()
            overlay = self.annotate_worker_preview(
                state,
                frame,
                worker_state=worker_state,
                use_worker_frame=False,
                detection_area=info.get("detection_area"),
                temporary=True,
            )
            pixmap = self.frame_to_pixmap(overlay)
            state.video_label.setPixmap(pixmap.scaled(state.video_label.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))
        except (OSError, json.JSONDecodeError):
            return

    def restore_permanent_gate_preview(self, state):
        state.worker_state_path = self.cpp_detection.state_path(
            self.normalized_gate_id(state.parking_id, state.gate_type, state.gate_name)
        )
        state.latest_frame = None
        state.video_label.clear()
        gate_id = self.normalized_gate_id(state.parking_id, state.gate_type, state.gate_name)
        frame, worker_state = self.cpp_detection.read_preview(gate_id)
        if frame is not None:
            state.latest_frame = frame.copy()
            pixmap = self.frame_to_pixmap(self.annotate_worker_preview(state, frame, worker_state=worker_state, use_worker_frame=False))
            state.video_label.setPixmap(pixmap.scaled(state.video_label.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))
        elif state.expanded:
            self.start_stream(state)
        else:
            state.video_label.setText("Permanent gate preview restored.")

    def update_gate_worker_status(self, parking_id, gate_type, gate_name):
        gate_id = self.normalized_gate_id(parking_id, gate_type, gate_name)
        running = self.cpp_detection.is_running(gate_id)
        text = "● RUNNING" if running else "● STOPPED"
        color = "#2ecc71" if running else "#ff5f56"
        for state_map in (self.settings_gate_states, self.live_gate_states):
            for state in state_map.values():
                if (
                    state.parking_id == parking_id
                    and state.gate_type == gate_type
                    and state.gate_name == gate_name
                ):
                    state.worker_status_label.setText(text)
                    state.worker_status_label.setStyleSheet(
                        f"color:{color}; font-weight:700; padding:4px 8px;"
                    )

    def update_all_worker_statuses(self):
        seen = set()
        for state_map in (self.settings_gate_states, self.live_gate_states):
            for state in state_map.values():
                identity = (state.parking_id, state.gate_type, state.gate_name)
                if identity not in seen:
                    seen.add(identity)
                    self.update_gate_worker_status(*identity)

    def kill_gate_workers(self, parking_id, gate_type, gate_name):
        gate_id = self.normalized_gate_id(parking_id, gate_type, gate_name)
        self.cpp_detection.stop_gate(gate_id)
        self.worker_status_cache[gate_id] = "stopped"
        self.worker_failed_loader_signatures.pop(gate_id, None)
        self.active_media_gate_ids.discard(gate_id)
        for state_map in (self.settings_gate_states, self.live_gate_states):
            for state in state_map.values():
                if (
                    state.parking_id == parking_id
                    and state.gate_type == gate_type
                    and state.gate_name == gate_name
                ):
                    self.stop_stream(state)
                    state.video_label.setText("Worker stopped")
        self.update_gate_worker_status(parking_id, gate_type, gate_name)
        self.log_message(f"Killed all workers for {gate_name} ({gate_type}).")

    def get_client_session_token(self):
        token = self.token_edit.text().strip() or CLIENT_SESSION_TOKEN
        if token.startswith('"') and token.endswith('"') and len(token) >= 2:
            token = token[1:-1].strip()
        return token

    def build_supabase_headers(self):
        token = self.get_client_session_token()
        headers = {"Content-Type": "application/json"}
        if SUPABASE_ANON_KEY:
            headers["apikey"] = SUPABASE_ANON_KEY
            headers["Authorization"] = f"Bearer {SUPABASE_ANON_KEY}"
        if token:
            headers["x-client-token"] = token
        return headers

    def fetch_remote_parkings(self):
        token = self.get_client_session_token()
        if not token:
            raise RuntimeError("Client session token is missing.")

        response = self.http.post(
            f"{SUPABASE_REST_URL}/rest/v1/rpc/view_parking_details",
            headers=self.build_supabase_headers(),
            json={},
            timeout=(5, 20),
        )
        if not response.ok:
            raise RuntimeError(f"Supabase parking lookup failed ({response.status_code}): {response.text}")
        return response.json()

    def _active_parkings(self):
        return [parking for parking in self.remote_parkings if parking.get("status") == "active"]

    def _stored_parking_ids(self):
        ids = []
        raw = self.device_config.get("parking_ids")
        if isinstance(raw, list):
            for value in raw:
                text = str(value or "").strip()
                if text and text not in ids:
                    ids.append(text)
        single = str(self.device_config.get("parking_id") or "").strip()
        if single and single not in ids:
            ids.append(single)
        return ids

    def _populate_parking_combo(self, preferred_parking_ids=None):
        preferred = []
        if isinstance(preferred_parking_ids, str) and preferred_parking_ids:
            preferred = [preferred_parking_ids]
        elif preferred_parking_ids:
            preferred = [str(value) for value in preferred_parking_ids if value]
        if not preferred:
            preferred = self._selected_parking_ids() or self._stored_parking_ids()
        preferred_set = set(preferred)
        self.parking_list.blockSignals(True)
        self.parking_list.clear()
        for parking in self._active_parkings():
            parking_id = parking.get("parking_id")
            item = QListWidgetItem(f'{parking.get("parking_name")} | {parking_id}')
            item.setData(Qt.ItemDataRole.UserRole, parking_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if parking_id in preferred_set else Qt.CheckState.Unchecked)
            self.parking_list.addItem(item)
        self.parking_list.blockSignals(False)

    def _selected_parking_ids(self):
        ids = []
        for index in range(self.parking_list.count()):
            item = self.parking_list.item(index)
            if item is None or item.checkState() != Qt.CheckState.Checked:
                continue
            parking_id = item.data(Qt.ItemDataRole.UserRole)
            if parking_id and parking_id not in ids:
                ids.append(parking_id)
        return ids

    def _selected_parking_id(self):
        ids = self._selected_parking_ids()
        return ids[0] if ids else None

    def _combined_gate_signature(self):
        return tuple(self.gate_signature(self._parking_by_id(parking_id)) for parking_id in self.locked_parking_ids)

    def reload_remote_parkings(self, initial_load=False):
        try:
            parkings = self.fetch_remote_parkings()
            self.remote_parkings = parkings or []

            if self.locked_parking_ids:
                if not initial_load:
                    signature = self._combined_gate_signature()
                    if signature != self.locked_gate_signature:
                        self.render_gate_grids()
            else:
                preferred = self._selected_parking_ids() or self._stored_parking_ids()
                self._populate_parking_combo(preferred)
                if initial_load and preferred:
                    self.lock_parking(persist=False)

            self.log_message(f"Loaded {len(self.remote_parkings)} parking location(s).")
            if initial_load:
                self.refresh_timer.start(PARKING_REFRESH_INTERVAL_MS)
        except Exception as error:
            self.log_message(f"Supabase parking refresh deferred; keeping current locked parking and gates: {error}")

    def poll_remote_parking_changes(self):
        self.reload_remote_parkings(initial_load=False)

    def save_session_token(self):
        token = self.get_client_session_token()
        self.device_config["client_session_token"] = token
        self.save_device_config()
        self.log_message("Client session token saved.")
        self.reload_remote_parkings()

    def save_api_key(self):
        api_key = self.api_key_edit.text().strip()
        if not api_key.startswith("pk_live_") or len(api_key) < 40:
            QMessageBox.warning(self, "API Key", "Enter a valid gate API key.")
            return
        self.device_config["parking_api_key"] = api_key
        self.save_device_config()
        self.write_worker_config()
        self.sync_cpp_workers_all()
        self.log_message("Gate API key saved for the native worker.")

    def lock_parking(self, persist=True):
        parking_ids = self._selected_parking_ids()
        if not parking_ids:
            QMessageBox.warning(self, "Parking", "Select at least one parking location first.")
            return

        self.locked_parking_ids = parking_ids
        self.parking_list.setEnabled(False)

        try:
            self.lock_button.clicked.disconnect()
        except Exception:
            pass
        self.lock_button.setText("Unlock Parking")
        self.lock_button.clicked.connect(self.unlock_parking)

        labels = [
            self.parking_list.item(index).text()
            for index in range(self.parking_list.count())
            if self.parking_list.item(index) and self.parking_list.item(index).checkState() == Qt.CheckState.Checked
        ]
        if persist:
            self.device_config["parking_ids"] = parking_ids
            self.device_config["parking_id"] = parking_ids[0]
            self.save_device_config()
            self.log_message(f"Parking locked to {', '.join(labels)}.")

        for parking_id in parking_ids:
            self.check_device_session(parking_id)
        self.render_gate_grids()

    def check_device_session(self, parking_id):
        api_key = self.api_key_edit.text().strip() or self.device_config.get("parking_api_key") or PARKING_API_KEY
        if not api_key or not parking_id:
            return None
        url = f"{worker_base_url()}/barrier/device-session"
        try:
            response = self.http.get(
                url,
                headers={"X-API-Key": api_key, "X-Parking-Id": parking_id},
                timeout=12,
            )
        except Exception as error:
            self.log_message(f"Device session check failed: {error}")
            return None
        if response.status_code == 200:
            self.log_message("Device session allowed for the locked parking.")
            return True
        detail = ""
        try:
            payload = response.json()
            detail = payload.get("detail") or payload.get("reason") or ""
        except ValueError:
            detail = (response.text or "").strip()
        suffix = f" ({detail})" if detail else ""
        self.log_message(f"Device session denied HTTP {response.status_code}{suffix}. Controller will stay connected and wait for parking activation.")
        if response.status_code in (401, 403):
            QMessageBox.warning(
                self,
                "Parking Access",
                "This parking is inactive or the API key is not allowed. The controller will keep listening and resume when access is granted.",
            )
        return False

    def unlock_parking(self):
        self.stop_all_streams(self.settings_gate_states)
        self.stop_all_streams(self.live_gate_states)
        self.cpp_detection.stop_all()
        self.locked_parking_ids = []
        self.locked_gate_signature = None
        self.parking_list.setEnabled(True)

        try:
            self.lock_button.clicked.disconnect()
        except Exception:
            pass
        self.lock_button.setText("Lock Parking")
        self.lock_button.clicked.connect(self.lock_parking)

        self.clear_grid(self.settings_grid, self.settings_gate_states)
        self.clear_grid(self.live_grid, self.live_gate_states)

    def _parking_by_id(self, parking_id):
        return next((item for item in self.remote_parkings if item.get("parking_id") == parking_id), None)

    @staticmethod
    def gate_signature(parking):
        if parking is None:
            return ((), ())
        return tuple((gate.get("gate_id"), gate.get("gate_name"), gate.get("gate_type"), gate.get("camera_url"), gate.get("barrier_url")) for gate in (parking.get("gates") or []))

    def clear_grid(self, grid, state_map):
        while grid.count():
            item = grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        state_map.clear()

    def normalized_gate_id(self, parking_id, gate_type, gate_name):
        parking = self._parking_by_id(parking_id) or {}
        gate = next((item for item in (parking.get("gates") or []) if item.get("gate_type") == gate_type and item.get("gate_name") == gate_name), None)
        return gate.get("gate_id") if gate else f"{parking_id}|{gate_type}|{gate_name}"

    @staticmethod
    def normalize_detection_area(detection_area):
        if not isinstance(detection_area, dict):
            return None
        keys = ("x", "y", "w", "h")
        try:
            values = {key: float(detection_area.get(key, 0.0)) for key in keys}
        except Exception:
            return None
        if values["w"] <= 0 or values["h"] <= 0:
            return None
        for key in keys:
            values[key] = max(0.0, min(1.0, values[key]))
        return values

    @staticmethod
    def detection_area_to_text(detection_area):
        area = ParkingQtApp.normalize_detection_area(detection_area)
        if area is None:
            return "ROI: not set"
        return f"ROI: x={area['x']:.2f}, y={area['y']:.2f}, w={area['w']:.2f}, h={area['h']:.2f}"

    def detection_worker_config(self, state):
        assignment = self.gate_assignments.get(state.parking_id, state.gate_name, state.gate_type) or {}
        gate_id = self.normalized_gate_id(state.parking_id, state.gate_type, state.gate_name)
        media_path = assignment.get("media_path")
        media_input_type = (assignment.get("media_input_type") or "").lower()
        use_media = bool(
            gate_id in self.active_media_gate_ids
            and media_path
            and os.path.isfile(media_path)
            and media_input_type in ("image", "video")
        )
        input_mode = "media" if use_media else "stream"
        input_path = media_path if use_media else assignment.get("camera_url")
        return {
            "input_mode": input_mode,
            "input_path": input_path,
            "entry_side": assignment.get("entry_side", "bottom"),
            "detection_area": self.normalize_detection_area(assignment.get("detection_area")),
            "parking_id": state.parking_id,
            "gate_name": state.gate_name,
            "gate_type": state.gate_type,
            "worker_url": WORKER_URL,
            "parking_api_key": self.api_key_edit.text().strip(),
            "client_token": self.get_client_session_token(),
            "vehicle_model_path": VEHICLE_MODEL_PATH,
            "plate_model_path": PLATE_MODEL_PATH,
            **self.normalize_detection_params(assignment.get("detection_params")),
        }

    @staticmethod
    def normalize_detection_params(params):
        source = params if isinstance(params, dict) else {}
        normalized = DEFAULT_DETECTION_PARAMS.copy()
        for key, default in normalized.items():
            try:
                normalized[key] = type(default)(source.get(key, default))
            except (TypeError, ValueError):
                pass
        normalized["line_ratio"] = max(0.01, min(0.45, normalized["line_ratio"]))
        normalized["track_iou_threshold"] = max(0.01, min(0.95, normalized["track_iou_threshold"]))
        normalized["vehicle_confidence"] = max(0.05, min(0.95, normalized["vehicle_confidence"]))
        normalized["vehicle_roi_percent"] = max(0.01, min(1.0, normalized["vehicle_roi_percent"]))
        for key in (
            "vehicle_detection_interval", "vehicle_exit_frames", "vehicle_stationary_frames",
            "plate_detection_interval", "plate_dwell_frames",
        ):
            normalized[key] = max(1, int(normalized[key]))
        return normalized

    def sync_cpp_worker_for_state(self, state):
        gate_id = self.normalized_gate_id(state.parking_id, state.gate_type, state.gate_name)
        config = self.detection_worker_config(state)
        signature = self.cpp_detection._config_signature(config)
        failed_signature = self.worker_failed_loader_signatures.get(gate_id)
        if failed_signature and failed_signature == signature:
            previous_status = self.worker_status_cache.get(gate_id)
            self.worker_status_cache[gate_id] = "loader_failed"
            if previous_status != "loader_failed":
                self.log_message(
                    f"Worker launch suppressed for {state.gate_name} ({state.gate_type}) after loader crash. "
                    "Change gate detection settings or fix runtime DLL mismatch to retry."
                )
            return
        if failed_signature and failed_signature != signature:
            self.worker_failed_loader_signatures.pop(gate_id, None)

        result = self.cpp_detection.sync_gate(gate_id, config)
        status = result.get("status", "unknown")
        previous_status = self.worker_status_cache.get(gate_id)
        self.worker_status_cache[gate_id] = status
        if status != previous_status:
            if status in ("started", "running"):
                self.log_message(
                    f"Worker {status} for {state.gate_name} ({state.gate_type}) pid={result.get('pid')}"
                )
                staged = result.get("staged_dlls") or []
                if staged:
                    self.log_message(f"Staged worker runtime DLLs: {', '.join(staged)}")
            elif status == "stopped":
                self.log_message(f"Worker stopped for {state.gate_name} ({state.gate_type})")

        if status == "missing_binary" and not self.cpp_detection.missing_binary_logged:
            self.cpp_detection.missing_binary_logged = True
            self.log_message(
                f"CPP detector binary not found at {CPP_DETECTOR_BIN}. Detection workers are idle."
            )
            self.update_gate_worker_status(state.parking_id, state.gate_type, state.gate_name)

    def sync_cpp_workers_all(self):
        self.write_worker_config()
        for state in self.settings_gate_states.values():
            self.sync_cpp_worker_for_state(state)

    def write_worker_config(self):
        parking_ids = list(self.locked_parking_ids)
        gates = []
        for parking_id in parking_ids:
            parking = self._parking_by_id(parking_id) or {}
            for gate in parking.get("gates") or []:
                assignment = self.gate_assignments.get(parking_id, gate.get("gate_name"), gate.get("gate_type")) or {}
                gates.append({**gate, **assignment, "parking_id": parking_id})
        payload = {
            "version": 1,
            "parking_id": parking_ids[0] if parking_ids else None,
            "parking_ids": parking_ids,
            "session_token": self.get_client_session_token(),
            "api_key": self.api_key_edit.text().strip(),
            "worker_url": WORKER_URL,
            "vehicle_model_path": VEHICLE_MODEL_PATH,
            "plate_model_path": PLATE_MODEL_PATH,
            "gates": gates,
        }
        previous = None
        try:
            with open(WORKER_CONFIG_PATH, "r", encoding="utf-8") as config_file:
                previous = json.load(config_file)
        except (OSError, json.JSONDecodeError):
            pass
        temporary = WORKER_CONFIG_PATH + ".tmp"
        with open(temporary, "w", encoding="utf-8") as config_file:
            json.dump(payload, config_file, indent=2)
        os.replace(temporary, WORKER_CONFIG_PATH)
        if previous != payload and self.cpp_detection.is_running("__controller__"):
            self.cpp_detection.stop_all()

    def save_remote_gate_endpoints(self, state, camera_url, barrier_url):
        gate_id = self.normalized_gate_id(state.parking_id, state.gate_type, state.gate_name)
        if "|" in gate_id:
            return
        response = self.http.post(
            f"{SUPABASE_REST_URL}/rest/v1/rpc/update_gate_endpoints",
            headers=self.build_supabase_headers(),
            json={"p_gate_id": gate_id, "p_camera_url": camera_url or None, "p_barrier_url": barrier_url or None},
            timeout=12,
        )
        if not response.ok:
            raise RuntimeError(f"Supabase gate update failed ({response.status_code}): {response.text}")

    def render_gate_grids(self):
        if not self.locked_parking_ids:
            return

        self.stop_all_streams(self.settings_gate_states)
        self.stop_all_streams(self.live_gate_states)
        self.clear_grid(self.settings_grid, self.settings_gate_states)
        self.clear_grid(self.live_grid, self.live_gate_states)

        parkings = [self._parking_by_id(parking_id) for parking_id in self.locked_parking_ids]
        parkings = [parking for parking in parkings if parking]
        if not parkings:
            self.settings_grid.addWidget(QLabel("Parking details are not available."), 0, 0)
            self.live_grid.addWidget(QLabel("Parking details are not available."), 0, 0)
            return

        self.locked_gate_signature = self._combined_gate_signature()
        row = 0
        for parking in parkings:
            parking_id = parking.get("parking_id")
            title = parking.get("parking_name") or parking_id
            self.settings_grid.addWidget(self.make_header(title, "#c4a7e7"), row, 0, 1, 2)
            self.live_grid.addWidget(self.make_header(title, "#c4a7e7"), row, 0, 1, 2)
            row += 1
            self.settings_grid.addWidget(self.make_header("Exit Gates", "#f6c177"), row, 0)
            self.settings_grid.addWidget(self.make_header("Entry Gates", "#8bd5ca"), row, 1)
            self.live_grid.addWidget(self.make_header("Exit Gates", "#f6c177"), row, 0)
            self.live_grid.addWidget(self.make_header("Entry Gates", "#8bd5ca"), row, 1)
            row += 1
            entry_gates = [gate.get("gate_name") for gate in (parking.get("gates") or []) if gate.get("gate_type") == "entry"]
            exit_gates = [gate.get("gate_name") for gate in (parking.get("gates") or []) if gate.get("gate_type") == "exit"]
            total_rows = max(len(exit_gates), len(entry_gates), 1)
            for row_index in range(total_rows):
                if row_index < len(exit_gates):
                    gate_name = exit_gates[row_index]
                    self.create_gate_card(self.settings_grid, self.settings_gate_states, parking_id, gate_name, "exit", row, 0, "settings")
                    self.create_gate_card(self.live_grid, self.live_gate_states, parking_id, gate_name, "exit", row, 0, "live")
                if row_index < len(entry_gates):
                    gate_name = entry_gates[row_index]
                    self.create_gate_card(self.settings_grid, self.settings_gate_states, parking_id, gate_name, "entry", row, 1, "settings")
                    self.create_gate_card(self.live_grid, self.live_gate_states, parking_id, gate_name, "entry", row, 1, "live")
                row += 1

        self.refresh_endpoint_menus()
        self.sync_cpp_workers_all()

    @staticmethod
    def make_header(title, color):
        header = QLabel(title)
        header.setStyleSheet(f"font-weight:700; color:{color};")
        return header

    def create_gate_card(self, grid, state_map, parking_id, gate_name, gate_type, row, column, mode):
        key = f"{parking_id}|{gate_type}|{gate_name}|{mode}"

        card = QFrame()
        card.setFrameShape(QFrame.Shape.StyledPanel)
        card.setStyleSheet("QFrame{background:#2a2e33; border:1px solid #444;} QLabel{color:#f2f2f2;}")
        card_layout = QVBoxLayout(card)

        header = QHBoxLayout()
        toggle_button = QPushButton(f"▶ {gate_name} ({gate_type.upper()})")
        toggle_button.setCheckable(True)
        toggle_button.setChecked(False)
        toggle_button.setStyleSheet("text-align:left; font-weight:700; padding:6px;")
        header.addWidget(toggle_button, stretch=1)
        worker_status_label = QLabel("● STOPPED")
        worker_status_label.setStyleSheet("color:#ff5f56; font-weight:700; padding:4px 8px;")
        header.addWidget(worker_status_label)
        kill_worker_btn = QPushButton("Kill Worker")
        kill_worker_btn.setStyleSheet("color:#fff; background:#8b1e1e; padding:4px 8px;")
        kill_worker_btn.clicked.connect(
            lambda _checked=False, pid=parking_id, gt=gate_type, gn=gate_name: self.kill_gate_workers(pid, gt, gn)
        )
        header.addWidget(kill_worker_btn)
        card_layout.addLayout(header)

        content = QWidget()
        content_layout = QVBoxLayout(content)

        camera_row = QHBoxLayout()
        camera_row.addWidget(QLabel("Camera URL"))
        camera_combo = QComboBox()
        camera_combo.setEditable(True)
        camera_row.addWidget(camera_combo, stretch=1)
        content_layout.addLayout(camera_row)

        barrier_combo = None
        entry_side_combo = None
        detection_summary_label = None
        detection_controls = None
        if mode == "settings":
            barrier_row = QHBoxLayout()
            barrier_row.addWidget(QLabel("Barrier URL"))
            barrier_combo = QComboBox()
            barrier_combo.setEditable(True)
            barrier_row.addWidget(barrier_combo, stretch=1)
            content_layout.addLayout(barrier_row)

            detection_row = QHBoxLayout()
            detection_row.addWidget(QLabel("Vehicle Entry Side"))
            entry_side_combo = QComboBox()
            entry_side_combo.addItems(["top", "bottom"])
            detection_row.addWidget(entry_side_combo)

            draw_area_btn = QPushButton("Draw Detection Area")
            draw_area_btn.clicked.connect(lambda: self.draw_detection_area_for_gate(key, state_map))
            detection_row.addWidget(draw_area_btn)
            detection_row.addStretch()
            content_layout.addLayout(detection_row)

            detection_summary_label = QLabel("ROI: not set")
            detection_summary_label.setStyleSheet("color:#d9d9d9;")
            content_layout.addWidget(detection_summary_label)

            detection_controls = {}
            params_grid = QGridLayout()
            specs = (
                ("line_ratio", "Entry line offset %", 1.0, 45.0, 1.0, 10.0),
                ("vehicle_detection_interval", "Vehicle interval", 1, 120, 1, 10),
                ("vehicle_exit_frames", "Exit frames", 1, 300, 1, 20),
                ("vehicle_stationary_frames", "Stationary frames", 1, 600, 1, 50),
                ("vehicle_roi_percent", "Vehicle inside ROI %", 1.0, 100.0, 1.0, 50.0),
                ("plate_detection_interval", "Plate interval", 1, 120, 1, 10),
                ("plate_dwell_frames", "Plate dwell frames", 2, 300, 1, 10),
                ("track_iou_threshold", "Track IoU", 0.01, 0.95, 0.01, 0.15),
                ("vehicle_confidence", "Vehicle confidence", 0.05, 0.95, 0.05, 0.35),
            )
            for index, (name, label, minimum, maximum, step, default) in enumerate(specs):
                params_grid.addWidget(QLabel(label), index // 2, (index % 2) * 2)
                if isinstance(default, float):
                    control = QDoubleSpinBox()
                    control.setDecimals(2)
                else:
                    control = QSpinBox()
                control.setRange(minimum, maximum)
                control.setSingleStep(step)
                control.setValue(default)
                params_grid.addWidget(control, index // 2, (index % 2) * 2 + 1)
                detection_controls[name] = control
            content_layout.addLayout(params_grid)

        buttons = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(lambda: self.save_gate_endpoints(key, state_map))
        buttons.addWidget(save_btn)

        if mode == "settings":
            barrier_btn = QPushButton("Open Barrier")
            barrier_btn.clicked.connect(lambda: self.open_barrier(key, state_map))
            buttons.addWidget(barrier_btn)

            stream_btn = QPushButton("Capture Camera")
            stream_btn.clicked.connect(lambda: self.start_stream_for_state(key, state_map))
            buttons.addWidget(stream_btn)

            media_btn = QPushButton("Load Image/Video")
            media_btn.clicked.connect(lambda: self.load_media(key, state_map))
            buttons.addWidget(media_btn)

        stop_btn = QPushButton("Stop")
        stop_btn.clicked.connect(lambda: self.stop_stream(state_map.get(key)))
        buttons.addWidget(stop_btn)
        buttons.addStretch()
        content_layout.addLayout(buttons)

        video_label = QLabel("Collapsed")
        video_label.setMinimumHeight(240)
        video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        video_label.setStyleSheet("background:#111; border:1px solid #333;")
        content_layout.addWidget(video_label)

        content.setVisible(False)
        card_layout.addWidget(content)

        grid.addWidget(card, row, column)

        state = GateCardState(
            key=key,
            parking_id=parking_id,
            gate_name=gate_name,
            gate_type=gate_type,
            mode=mode,
            card=card,
            toggle_button=toggle_button,
            content=content,
            camera_combo=camera_combo,
            barrier_combo=barrier_combo,
            entry_side_combo=entry_side_combo,
            detection_summary_label=detection_summary_label,
            detection_controls=detection_controls,
            video_label=video_label,
            worker_status_label=worker_status_label,
        )
        state_map[key] = state
        state.worker_state_path = self.cpp_detection.state_path(
            self.normalized_gate_id(parking_id, gate_type, gate_name)
        )

        assignment = self.gate_assignments.get(parking_id, gate_name, gate_type) or {}
        state.media_path = assignment.get("media_path")
        state.media_input_type = assignment.get("media_input_type")
        self.set_endpoint_selection(
            state,
            assignment.get("camera_url", ""),
            assignment.get("barrier_url", ""),
        )

        toggle_button.toggled.connect(lambda expanded, k=key: self.set_card_expanded(k, state_map, expanded))
        self.update_gate_worker_status(parking_id, gate_type, gate_name)

    def set_card_expanded(self, key, state_map, expanded):
        state = state_map.get(key)
        if state is None:
            return

        state.expanded = expanded
        state.content.setVisible(expanded)
        arrow = "▼" if expanded else "▶"
        state.toggle_button.setText(f"{arrow} {state.gate_name} ({state.gate_type.upper()})")

        if not expanded:
            self.stop_stream(state)
            state.video_label.setText("Collapsed")
            return

        if state.mode == "live":
            self.start_stream(state)
        else:
            state.video_label.setText("Expanded. Click Capture Camera or Load Image/Video.")

    def set_all_expanded(self, state_map, expanded):
        for state in state_map.values():
            state.toggle_button.setChecked(expanded)

    def set_endpoint_selection(self, state, camera_url, barrier_url):
        if camera_url:
            state.camera_combo.setEditText(self.camera_label_for_url(camera_url))
        if state.barrier_combo is not None and barrier_url:
            state.barrier_combo.setEditText(self.barrier_label_for_url(barrier_url))

        assignment = self.gate_assignments.get(state.parking_id, state.gate_name, state.gate_type) or {}
        if state.entry_side_combo is not None:
            entry_side = assignment.get("entry_side", "bottom")
            index = state.entry_side_combo.findText(entry_side)
            state.entry_side_combo.setCurrentIndex(index if index >= 0 else 1)
        if state.detection_summary_label is not None:
            state.detection_summary_label.setText(
                self.detection_area_to_text(assignment.get("detection_area"))
            )
        if state.detection_controls:
            params = self.normalize_detection_params(assignment.get("detection_params"))
            for name, control in state.detection_controls.items():
                control.setValue(params[name] * 100 if name in ("line_ratio", "vehicle_roi_percent") else params[name])

    def camera_label_for_url(self, url):
        for item in self.endpoint_registry.by_kind("camera"):
            if item.get("url") == url:
                return f'{item.get("name")} | {url}'
        return url

    def barrier_label_for_url(self, url):
        for item in self.endpoint_registry.by_kind("barrier"):
            if item.get("url") == url:
                return f'{item.get("name")} | {url}'
        return url

    def refresh_endpoint_menus(self):
        camera_values = [f'{item["name"]} | {item["url"]}' for item in self.endpoint_registry.by_kind("camera")]
        barrier_values = [f'{item["name"]} | {item["url"]}' for item in self.endpoint_registry.by_kind("barrier")]

        for state_map in (self.settings_gate_states, self.live_gate_states):
            for state in state_map.values():
                current_camera = state.camera_combo.currentText()
                state.camera_combo.clear()
                state.camera_combo.addItems(camera_values)
                state.camera_combo.setEditText(current_camera)

                if state.barrier_combo is not None:
                    current_barrier = state.barrier_combo.currentText()
                    state.barrier_combo.clear()
                    state.barrier_combo.addItems(barrier_values)
                    state.barrier_combo.setEditText(current_barrier)

                assignment = self.gate_assignments.get(state.parking_id, state.gate_name, state.gate_type) or {}
                if assignment:
                    self.set_endpoint_selection(
                        state,
                        assignment.get("camera_url", ""),
                        assignment.get("barrier_url", ""),
                    )

    def discover_endpoints(self):
        try:
            discovered = self.endpoint_registry.discover()
            self.refresh_endpoint_menus()
            self.log_message(f"Discovered {len(discovered)} endpoint(s).")
        except Exception as error:
            QMessageBox.critical(self, "Discover", str(error))

    def add_manual_endpoint(self):
        kind, ok = QInputDialog.getText(self, "Endpoint Type", "Enter camera or barrier:")
        if not ok:
            return
        kind = (kind or "").strip().lower()
        if kind not in ("camera", "barrier"):
            QMessageBox.warning(self, "Endpoint", "Type must be camera or barrier.")
            return

        url, ok = QInputDialog.getText(self, "Endpoint URL", "Enter endpoint URL or camera index:")
        if not ok:
            return
        url = (url or "").strip()
        if not url:
            return

        name, ok = QInputDialog.getText(self, "Endpoint Name", "Enter endpoint name (optional):")
        if not ok:
            return
        name = (name or "").strip() or url

        self.endpoint_registry.add(kind, name, url)
        self.refresh_endpoint_menus()
        self.log_message(f"Saved manual {kind} endpoint: {url}")

    @staticmethod
    def selected_endpoint(value):
        endpoint = value.rsplit(" | ", 1)[-1] if " | " in value else value
        return endpoint.strip() or None

    def save_gate_endpoints(self, key, state_map):
        state = state_map.get(key)
        if state is None:
            return

        assignment = self.gate_assignments.get(state.parking_id, state.gate_name, state.gate_type) or {}
        camera_url = self.selected_endpoint(state.camera_combo.currentText()) or assignment.get("camera_url", "")
        media_path = assignment.get("media_path")
        media_input_type = assignment.get("media_input_type")
        camera_changed = bool(camera_url and camera_url != assignment.get("camera_url"))
        if camera_changed:
            media_path = ""
            media_input_type = ""
        if not camera_url and not media_path:
            QMessageBox.warning(self, "Save", "Set a camera endpoint or upload media for this gate.")
            return

        barrier_url = assignment.get("barrier_url", "")
        detection_area = self.normalize_detection_area(assignment.get("detection_area"))
        entry_side = assignment.get("entry_side", "bottom")
        detection_params = self.normalize_detection_params(assignment.get("detection_params"))
        if state.entry_side_combo is not None:
            entry_side = state.entry_side_combo.currentText().strip().lower() or "bottom"
        if state.detection_controls:
            detection_params = {
                name: control.value() / 100.0 if name in ("line_ratio", "vehicle_roi_percent") else control.value()
                for name, control in state.detection_controls.items()
            }
        if state.barrier_combo is not None:
            selected_barrier = self.selected_endpoint(state.barrier_combo.currentText())
            if not selected_barrier:
                QMessageBox.warning(self, "Save", "Barrier endpoint is required in Settings tab.")
                return
            barrier_url = selected_barrier

        known_cameras = {item.get("url") for item in self.endpoint_registry.by_kind("camera")}
        known_barriers = {item.get("url") for item in self.endpoint_registry.by_kind("barrier")}
        if camera_url not in known_cameras:
            self.endpoint_registry.add("camera", f"Manual camera {camera_url}", camera_url)
        if barrier_url and barrier_url not in known_barriers:
            self.endpoint_registry.add("barrier", f"Manual barrier {barrier_url}", barrier_url)

        self.gate_assignments.set(
            state.parking_id,
            state.gate_name,
            state.gate_type,
            camera_url,
            barrier_url,
            detection_area=detection_area,
            entry_side=entry_side,
            media_path=media_path,
            media_input_type=media_input_type,
            detection_params=detection_params,
        )
        try:
            self.save_remote_gate_endpoints(state, camera_url, barrier_url)
        except Exception as error:
            QMessageBox.critical(self, "Save", str(error))
            return
        self.write_worker_config()
        self.refresh_endpoint_menus()
        self.sync_cpp_worker_for_state(state)
        self.refresh_gate_preview(state)
        if camera_changed and state.expanded:
            self.start_stream(state)
        self.log_message(f"Saved endpoints for {state.gate_name} ({state.gate_type}).")

    def draw_detection_area_for_gate(self, key, state_map):
        state = state_map.get(key)
        if state is None:
            return

        assignment = self.gate_assignments.get(state.parking_id, state.gate_name, state.gate_type) or {}
        selected_camera = self.selected_endpoint(state.camera_combo.currentText())
        camera_changed = bool(selected_camera and selected_camera != assignment.get("camera_url"))
        gate_id = self.normalized_gate_id(state.parking_id, state.gate_type, state.gate_name)
        media_path = assignment.get("media_path") if gate_id in self.active_media_gate_ids and not camera_changed else None
        media_kind = (assignment.get("media_input_type") or "").lower()
        source = selected_camera or assignment.get("camera_url")

        frame = state.latest_frame.copy() if camera_changed and state.latest_frame is not None else None
        if frame is None and media_path and os.path.isfile(media_path) and media_kind in ("image", "video"):
            if media_kind == "image":
                frame = cv2.imread(media_path)
            else:
                media_capture = cv2.VideoCapture(media_path)
                if media_capture.isOpened():
                    ok, sampled = media_capture.read()
                    if ok:
                        frame = sampled
                media_capture.release()
            if frame is None:
                QMessageBox.warning(self, "Detection Area", "Unable to read uploaded media for ROI selection.")
                return
        elif frame is None:
            if not source:
                QMessageBox.warning(self, "Detection Area", "Save/select camera or upload media first.")
                return
            capture = self.open_capture_from_source(source)
            if capture is None:
                QMessageBox.warning(self, "Detection Area", "Unable to open camera for ROI selection.")
                return
            ok, sampled = capture.read()
            capture.release()
            if not ok or sampled is None:
                QMessageBox.warning(self, "Detection Area", "Unable to grab frame for ROI selection.")
                return
            frame = sampled

        params = self.normalize_detection_params(assignment.get("detection_params"))
        if state.detection_controls:
            params["line_ratio"] = state.detection_controls["line_ratio"].value() / 100.0
        dialog = DetectionRoiDialog(frame, state.gate_name, params["line_ratio"], parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.log_message(f"Detection area selection cancelled for {state.gate_name}.")
            return

        normalized = dialog.selected_normalized_rect()
        if normalized is None:
            self.log_message(f"Detection area selection cancelled for {state.gate_name}.")
            return

        x = int(normalized["x"] * frame.shape[1])
        y = int(normalized["y"] * frame.shape[0])
        w = int(normalized["w"] * frame.shape[1])
        h = int(normalized["h"] * frame.shape[0])
        if w <= 0 or h <= 0:
            self.log_message(f"Detection area selection cancelled for {state.gate_name}.")
            return

        detection_area = self.normalize_detection_area(normalized)
        entry_side = "bottom"
        if state.entry_side_combo is not None:
            entry_side = state.entry_side_combo.currentText().strip().lower() or "bottom"

        barrier_url = assignment.get("barrier_url", "")
        self.gate_assignments.set(
            state.parking_id,
            state.gate_name,
            state.gate_type,
            source,
            barrier_url,
            detection_area=detection_area,
            entry_side=entry_side,
            media_path=media_path,
            media_input_type=media_kind if media_path and media_kind in ("image", "video") else "",
            detection_params=params,
        )

        self.refresh_endpoint_menus()
        self.sync_cpp_worker_for_state(state)
        self.refresh_gate_preview(state)
        self.log_message(f"Detection area saved for {state.gate_name} ({state.gate_type}).")

    @staticmethod
    def open_capture_from_source(source):
        if source.isdigit():
            capture = cv2.VideoCapture(int(source), cv2.CAP_DSHOW)
            try:
                capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            return capture if capture.isOpened() else None

        attempts = [
            lambda: cv2.VideoCapture(source),
            lambda: cv2.VideoCapture(source, cv2.CAP_FFMPEG),
            lambda: cv2.VideoCapture(source, cv2.CAP_MSMF),
        ]
        for create_cap in attempts:
            capture = create_cap()
            if capture.isOpened():
                try:
                    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass
                return capture
            capture.release()
        return None

    @staticmethod
    def is_realtime_source(source):
        if source is None:
            return False
        value = source.strip().lower()
        if not value:
            return False
        if value.isdigit():
            return True
        prefixes = ("rtsp://", "http://", "https://", "udp://", "tcp://", "rtmp://")
        return value.startswith(prefixes)

    def stop_stream(self, state):
        if state is None:
            return

        if state.shared_source:
            self.detach_shared_stream(state)

        if state.timer is not None:
            state.timer.stop()
            state.timer.deleteLater()
            state.timer = None
        if state.capture is not None:
            state.capture.release()
            state.capture = None

    def get_state_by_key(self, key):
        if key in self.settings_gate_states:
            return self.settings_gate_states[key]
        return self.live_gate_states.get(key)

    def attach_shared_stream(self, state, source):
        stream = self.shared_streams.get(source)
        if stream is None:
            capture = self.open_capture_from_source(source)
            if capture is None:
                state.video_label.setText("Unable to open stream.")
                return
            timer = QTimer(self)
            stream = SharedStreamState(
                source=source,
                capture=capture,
                timer=timer,
                subscribers=set(),
                latest_frame=None,
                frame_lock=threading.Lock(),
                reader_thread=None,
                running=True,
            )
            self.shared_streams[source] = stream
            stream.reader_thread = threading.Thread(
                target=self.shared_stream_reader_loop,
                args=(source,),
                daemon=True,
            )
            stream.reader_thread.start()
            timer.timeout.connect(lambda src=source: self.render_shared_stream_frame(src))
            timer.start(max(25, int(1000 / max(1, self.current_wall_fps()))))

        stream.subscribers.add(state.key)
        state.shared_source = source

    def detach_shared_stream(self, state):
        source = state.shared_source
        if not source:
            return

        stream = self.shared_streams.get(source)
        if stream is not None:
            stream.subscribers.discard(state.key)
            if not stream.subscribers:
                stream.running = False
                stream.timer.stop()
                stream.timer.deleteLater()
                if stream.reader_thread is not None:
                    stream.reader_thread.join(timeout=0.4)
                stream.capture.release()
                self.shared_streams.pop(source, None)

        state.shared_source = None

    def shared_stream_reader_loop(self, source):
        while True:
            stream = self.shared_streams.get(source)
            if stream is None or not stream.running:
                return

            frame = None
            ok = False
            for _ in range(4):
                if not stream.capture.grab():
                    break
                ok, latest = stream.capture.retrieve()
                if ok:
                    frame = latest

            if frame is None:
                ok, frame = stream.capture.read()

            if not ok or frame is None:
                time.sleep(0.02)
                continue

            if frame.shape[1] > LIVE_WALL_PREVIEW_WIDTH:
                ratio = LIVE_WALL_PREVIEW_WIDTH / frame.shape[1]
                preview_h = max(1, int(frame.shape[0] * ratio))
                frame = cv2.resize(frame, (LIVE_WALL_PREVIEW_WIDTH, preview_h), interpolation=cv2.INTER_AREA)

            if stream.frame_lock is not None:
                with stream.frame_lock:
                    stream.latest_frame = frame

    def render_shared_stream_frame(self, source):
        stream = self.shared_streams.get(source)
        if stream is None:
            return

        if stream.frame_lock is None:
            return

        with stream.frame_lock:
            frame = stream.latest_frame

        if frame is None:
            return

        now = time.time()
        min_paint_interval = 1.0 / max(1, self.current_wall_fps())

        for key in list(stream.subscribers):
            state = self.get_state_by_key(key)
            if state is None or not state.expanded:
                stream.subscribers.discard(key)
                continue
            if state.mode == "live" and (now - state.last_paint_ts) < min_paint_interval:
                continue
            state.latest_frame = frame.copy()
            pixmap = self.frame_to_pixmap(self.annotate_worker_preview(state, frame))
            state.video_label.setPixmap(
                pixmap.scaled(
                    state.video_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.FastTransformation,
                )
            )
            state.last_paint_ts = now

        if not stream.subscribers:
            stream.running = False
            stream.timer.stop()
            stream.timer.deleteLater()
            if stream.reader_thread is not None:
                stream.reader_thread.join(timeout=0.4)
            stream.capture.release()
            self.shared_streams.pop(source, None)

    def stop_all_streams(self, state_map):
        for state in state_map.values():
            self.stop_stream(state)

    @staticmethod
    def reset_detection_runtime(state):
        state.latest_frame = None

    def annotate_worker_preview(self, state, frame, worker_state=None, use_worker_frame=True, detection_area=None, temporary=False):
        gate_id = self.normalized_gate_id(state.parking_id, state.gate_type, state.gate_name)
        if worker_state is None:
            worker_frame, worker_state = self.cpp_detection.read_preview(gate_id)
            if use_worker_frame and worker_frame is not None:
                frame = worker_frame
        annotated = frame.copy()
        assignment = self.gate_assignments.get(state.parking_id, state.gate_name, state.gate_type) or {}
        area = self.normalize_detection_area(detection_area if detection_area is not None else assignment.get("detection_area"))
        height, width = frame.shape[:2]
        if area is not None:
            x1 = round(area["x"] * width)
            y1 = round(area["y"] * height)
            x2 = round((area["x"] + area["w"]) * width)
            y2 = round((area["y"] + area["h"]) * height)
            params = self.normalize_detection_params(assignment.get("detection_params"))
            entry_side = assignment.get("entry_side", "bottom")
            line_y = round(y1 + (y2 - y1) * params["line_ratio"])
            if entry_side == "bottom":
                line_y = round(y2 - (y2 - y1) * params["line_ratio"])
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (255, 255, 0), 2)
            cv2.line(annotated, (x1, line_y), (x2, line_y), (255, 255, 0), 2)
            cv2.putText(
                annotated,
                f"{'TEMP ' if temporary else ''}ROI | {entry_side.upper()} {params['line_ratio'] * 100:.0f}%",
                (x1 + 4, max(20, y1 - 7)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 0),
                2,
            )

        def pixel_box(box):
            return (
                round(float(box.get("x", 0)) * width),
                round(float(box.get("y", 0)) * height),
                round(float(box.get("x", 0) + box.get("w", 0)) * width),
                round(float(box.get("y", 0) + box.get("h", 0)) * height),
            )

        for vehicle in worker_state.get("vehicles", []):
            box = pixel_box(vehicle.get("box", {}))
            status = vehicle.get("status", "detected")
            color = (0, 255, 0) if status == "active" else (255, 0, 0)
            label = "ACTIVE VEHICLE" if status == "active" else "INACTIVE VEHICLE"
            cv2.rectangle(annotated, (box[0], box[1]), (box[2], box[3]), color, 2)
            cv2.putText(annotated, label, (box[0], max(20, box[1] - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)

        plate = worker_state.get("plate")
        if isinstance(plate, dict):
            box = pixel_box(plate)
            cv2.rectangle(annotated, (box[0], box[1]), (box[2], box[3]), (0, 255, 255), 2)
            cv2.putText(annotated, "PLATE", (box[0], max(20, box[1] - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)
        published_ms = worker_state.get("published_ms")
        processing_ms = worker_state.get("processing_ms")
        if isinstance(published_ms, (int, float)):
            state_age_ms = max(0, int(time.time() * 1000 - published_ms))
            lag_color = (0, 255, 0) if state_age_ms < 500 else (0, 255, 255) if state_age_ms < 1500 else (0, 0, 255)
            lag_text = f"CPP {int(processing_ms or 0)} ms | age {state_age_ms} ms"
            cv2.putText(annotated, lag_text, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, lag_color, 2)
        return annotated

    def refresh_gate_preview(self, state):
        for state_map in (self.settings_gate_states, self.live_gate_states):
            for candidate in state_map.values():
                if (
                    candidate.parking_id == state.parking_id
                    and candidate.gate_name == state.gate_name
                    and candidate.gate_type == state.gate_type
                    and candidate.latest_frame is not None
                ):
                    pixmap = self.frame_to_pixmap(self.annotate_worker_preview(candidate, candidate.latest_frame))
                    candidate.video_label.setPixmap(
                        pixmap.scaled(
                            candidate.video_label.size(),
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.FastTransformation,
                        )
                    )

    def start_stream(self, state):
        assignment = self.gate_assignments.get(state.parking_id, state.gate_name, state.gate_type) or {}
        source = assignment.get("camera_url")

        if not source:
            source = self.selected_endpoint(state.camera_combo.currentText())
            if source:
                self.gate_assignments.set(
                    state.parking_id,
                    state.gate_name,
                    state.gate_type,
                    source,
                    assignment.get("barrier_url", ""),
                    detection_area=self.normalize_detection_area(assignment.get("detection_area")),
                    entry_side=assignment.get("entry_side", "bottom"),
                )
                self.refresh_endpoint_menus()
                self.sync_cpp_worker_for_state(state)

        if not source:
            state.video_label.setText("No camera endpoint saved.")
            return

        self.stop_stream(state)
        self.reset_detection_runtime(state)
        self.sync_cpp_worker_for_state(state)
        state.timer = QTimer(self)
        state.timer.timeout.connect(lambda s=state: self.render_worker_preview(s))
        state.timer.start(FRAME_INTERVAL_MS)

    def render_worker_preview(self, state):
        gate_id = self.normalized_gate_id(state.parking_id, state.gate_type, state.gate_name)
        frame, worker_state = self.cpp_detection.read_preview(gate_id)
        if frame is None:
            return
        state.latest_frame = frame.copy()
        pixmap = self.frame_to_pixmap(
            self.annotate_worker_preview(state, frame, worker_state=worker_state, use_worker_frame=False)
        )
        state.video_label.setPixmap(
            pixmap.scaled(
                state.video_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
        )

    def current_wall_fps(self):
        value = LIVE_WALL_TARGET_FPS
        combo = getattr(self, "wall_fps_combo", None)
        if combo is not None:
            try:
                value = int(combo.currentText())
            except Exception:
                value = LIVE_WALL_TARGET_FPS
        return max(4, min(value, 24))

    def start_stream_for_state(self, key, state_map):
        state = state_map.get(key)
        if state is None:
            return
        selected_camera = self.selected_endpoint(state.camera_combo.currentText())
        assignment = self.gate_assignments.get(state.parking_id, state.gate_name, state.gate_type) or {}
        if selected_camera and (selected_camera != assignment.get("camera_url") or assignment.get("media_path")):
            self.active_media_gate_ids.discard(
                self.normalized_gate_id(state.parking_id, state.gate_type, state.gate_name)
            )
            self.gate_assignments.set(
                state.parking_id, state.gate_name, state.gate_type, selected_camera,
                assignment.get("barrier_url", ""),
                detection_area=self.normalize_detection_area(assignment.get("detection_area")),
                entry_side=assignment.get("entry_side", "bottom"),
                media_path="", media_input_type="",
                detection_params=self.normalize_detection_params(assignment.get("detection_params")),
            )
            self.sync_cpp_worker_for_state(state)
        if not state.expanded:
            state.toggle_button.setChecked(True)
        else:
            self.start_stream(state)

    def render_stream_frame(self, state):
        if state.shared_source:
            return
        if state.capture is None:
            return

        ok, frame = state.capture.read()
        if not ok:
            self.stop_stream(state)
            state.video_label.setText("Stream ended")
            return
        state.latest_frame = frame.copy()

        pixmap = self.frame_to_pixmap(self.annotate_worker_preview(state, frame))
        state.video_label.setPixmap(
            pixmap.scaled(
                state.video_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
        )

    def load_media(self, key, state_map):
        state = state_map.get(key)
        if state is None:
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            f"Select Image or Video for {state.gate_name}",
            "",
            "Images and Videos (*.jpg *.jpeg *.png *.bmp *.mp4 *.avi *.mov *.mkv *.wmv)",
        )
        if not file_path:
            return

        self.stop_stream(state)
        self.reset_detection_runtime(state)
        media_input_type = "video" if file_path.lower().endswith(VIDEO_EXTENSIONS) else "image"
        if media_input_type == "image":
            roi_frame = cv2.imread(file_path)
        else:
            capture = cv2.VideoCapture(file_path)
            ok, roi_frame = capture.read() if capture.isOpened() else (False, None)
            capture.release()
            if not ok:
                roi_frame = None
        if roi_frame is None:
            QMessageBox.critical(self, "Media", "Unable to read the selected file.")
            return

        assignment = self.gate_assignments.get(state.parking_id, state.gate_name, state.gate_type) or {}
        params = self.normalize_detection_params(assignment.get("detection_params"))
        dialog = DetectionRoiDialog(roi_frame, f"Temporary {state.gate_name}", params["line_ratio"], parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.log_message(f"Temporary {media_input_type} ROI selection cancelled for {state.gate_name}.")
            return
        detection_area = self.normalize_detection_area(dialog.selected_normalized_rect())
        if detection_area is None:
            QMessageBox.warning(self, "Temporary ROI", "Draw a valid detection area before continuing.")
            return

        state.media_path = None
        state.media_input_type = None
        self.start_temporary_media_worker(state, file_path, media_input_type, detection_area)

    def open_barrier(self, key, state_map):
        state = state_map.get(key)
        if state is None:
            return

        assignment = self.gate_assignments.get(state.parking_id, state.gate_name, state.gate_type) or {}
        barrier_url = assignment.get("barrier_url")
        if not barrier_url:
            QMessageBox.warning(self, "Barrier", "Save a barrier endpoint for this gate first.")
            return

        try:
            response = self.http.post(
                barrier_url,
                json={
                    "action": "open",
                    "parking_id": state.parking_id,
                    "gate": state.gate_name,
                    "gate_type": state.gate_type,
                },
                timeout=5,
            )
            response.raise_for_status()
            self.log_message(f"Barrier open accepted for gate '{state.gate_name}'.")
        except Exception as error:
            QMessageBox.critical(self, "Barrier", str(error))

    def closeEvent(self, event):
        self.stop_all_streams(self.settings_gate_states)
        self.stop_all_streams(self.live_gate_states)
        self.stop_all_temporary_media_workers()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ParkingQtApp()
    window.show()
    sys.exit(app.exec())
