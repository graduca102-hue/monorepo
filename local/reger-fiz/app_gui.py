import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from main import InstagramAndroidBot

CONFIG_FILE = "gui_config.json"
DOMAINS = [
    "gmail.com",
    "outlook.com",
    "yahoo.com",
    "hotmail.com",
    "mail.com",
]


def _find_adb_executable():
    """Находит ADB даже когда platform-tools не добавлен в PATH."""
    candidates = []
    path_adb = shutil.which("adb")
    if path_adb:
        candidates.append(Path(path_adb))

    app_dir = Path(__file__).resolve().parent
    python_dir = Path(sys.executable).resolve().parent
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    candidates.extend(
        [
            app_dir / "adb.exe",
            app_dir / "platform-tools" / "adb.exe",
            python_dir / "Lib" / "site-packages" / "adbutils" / "binaries" / "adb.exe",
            local_app_data / "Android" / "Sdk" / "platform-tools" / "adb.exe",
            Path(r"C:\Program Files\Laixi\tools\platform-tools\adb.exe"),
            Path(r"C:\Program Files (x86)\panda_android\tools\adb.exe"),
        ]
    )
    for candidate in candidates:
        if candidate and candidate.is_file():
            return str(candidate)
    return None


ADB_EXECUTABLE = _find_adb_executable()


def run_adb(args, timeout=8):
    if not ADB_EXECUTABLE:
        return False, "", "adb не найден (установите Android platform-tools)"
    try:
        result = subprocess.run(
            [ADB_EXECUTABLE] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return result.returncode == 0, (result.stdout or "").strip(), (result.stderr or "").strip()
    except FileNotFoundError:
        return False, "", "adb не найден в PATH"
    except subprocess.TimeoutExpired:
        return False, "", "adb timeout"
    except Exception as exc:
        return False, "", str(exc)


def _parse_adb_devices(output):
    """Разбирает adb devices [-l], включая USB, TCP/IP и mDNS serial."""
    devices = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("List of devices attached") or line.startswith("*"):
            continue

        # ADB обычно разделяет serial/status табом, но разные версии platform-tools
        # и обёртки могут использовать несколько обычных пробелов.
        fields = line.split()
        if len(fields) < 2:
            continue
        serial, status = fields[0], fields[1]
        if status != "device":
            continue

        properties = {}
        for field in fields[2:]:
            if ":" in field:
                key, value = field.split(":", 1)
                properties[key] = value
        devices[serial] = properties
    return devices


def _is_wifi_adb_serial(serial):
    # TCP/IP: 192.168.1.10:5555; Wireless debugging/mDNS:
    # adb-XXXX._adb-tls-connect._tcp или hostname:port.
    return ":" in serial or "._adb-tls-" in serial


def get_connected_devices():
    ok, stdout, _ = run_adb(["devices", "-l"])
    if not ok:
        return {}

    devices = {}
    for serial, properties in _parse_adb_devices(stdout).items():
        model = properties.get("model", "").replace("_", " ").strip()
        if model:
            devices[serial] = model
            continue

        model = serial
        ok_model, model_out, _ = run_adb(
            ["-s", serial, "shell", "getprop", "ro.product.model"],
            timeout=5,
        )
        if ok_model and model_out:
            model = model_out.strip() or serial
        devices[serial] = model
    return devices


class RegisterWorker(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()
    account_result_signal = pyqtSignal(dict)

    def __init__(
        self,
        accounts_count,
        devices,
        email_domain,
        email_mode,
        anymessage_token,
        smsbower_token,
        max_retries,
        install_method,
        device_root,
    ):
        super().__init__()
        self.accounts_count = accounts_count
        self.devices = list(devices)
        self.email_domain = email_domain
        self.email_mode = email_mode
        self.anymessage_token = anymessage_token
        self.smsbower_token = smsbower_token
        self.max_retries = max_retries
        self.install_method = install_method
        self.device_root = dict(device_root or {})
        self._stopped = False
        self._stop_lock = threading.Lock()
        self._executor = None

    def stop(self):
        with self._stop_lock:
            self._stopped = True

    def _is_stopped(self):
        with self._stop_lock:
            return self._stopped

    def log(self, msg):
        self.log_signal.emit(str(msg))

    def process_single_account(self, idx, device_serial):
        if self._is_stopped():
            return

        bot = None
        last_reason = "Неизвестная причина"

        try:
            self.log(f"Аккаунт {idx}/{self.accounts_count} → устройство {device_serial}")

            bot = InstagramAndroidBot(
                email_domain=self.email_domain,
                email_mode=self.email_mode,
                anymessage_token=self.anymessage_token,
                smsbower_token=self.smsbower_token,
                device_serial=device_serial,
                log_callback=self.log,
                stop_checker=self._is_stopped,
                max_retries=self.max_retries,
                install_method=self.install_method,
                root_enabled=self.device_root.get(device_serial, False),
            )

            if self._is_stopped():
                return

            success = bot.register()

            if success:
                self.log(f"✅ Аккаунт {idx}: регистрация успешна")
                self.account_result_signal.emit(
                    {
                        "idx": idx,
                        "success": True,
                        "device": device_serial,
                        "identifier": bot.email or bot.username or "",
                    }
                )
                return

            last_reason = "Регистрация не удалась"
            self.log(f"💥 Аккаунт {idx}: {last_reason}")
            if bot:
                bot.cleanup_after_failure()

        except Exception as exc:
            last_reason = f"Исключение: {exc}"
            self.log(f"✗ ОШИБКА аккаунт {idx}: {exc}")
            self.log(traceback.format_exc())
            if bot:
                try:
                    bot.cleanup_after_failure()
                except Exception:
                    pass

        if not self._is_stopped():
            self.account_result_signal.emit(
                {
                    "idx": idx,
                    "success": False,
                    "device": device_serial,
                    "identifier": getattr(bot, "email", None) if bot else "",
                    "reason": last_reason,
                }
            )

    def _process_device_queue(self, device_serial, account_indices):
        """Один поток на устройство — аккаунты на телефоне идут по очереди, без ожидания других телефонов."""
        for idx in account_indices:
            if self._is_stopped():
                return
            self.process_single_account(idx, device_serial)

    def run(self):
        try:
            effective_threads = len(self.devices)
            device_queues = {serial: [] for serial in self.devices}
            for idx in range(1, self.accounts_count + 1):
                serial = self.devices[(idx - 1) % len(self.devices)]
                device_queues[serial].append(idx)

            self.log(
                f"Запуск: {self.accounts_count} акк., потоков {effective_threads} "
                f"(= выбранных устройств, каждый телефон — свой поток)"
            )
            for serial, indices in device_queues.items():
                if len(indices) > 1:
                    self.log(
                        f"ℹ️ {serial}: в очереди {len(indices)} акк. "
                        f"({indices[0]}–{indices[-1]}) — только внутри этого телефона"
                    )

            with ThreadPoolExecutor(max_workers=effective_threads) as executor:
                self._executor = executor
                futures = [
                    executor.submit(self._process_device_queue, serial, indices)
                    for serial, indices in device_queues.items()
                    if indices
                ]

                for future in futures:
                    if self._is_stopped():
                        break
                    try:
                        future.result()
                    except Exception as exc:
                        self.log(f"Ошибка потока устройства (продолжаем): {exc}")

            self.log("=== Все задачи завершены ===")
        except Exception as exc:
            self.log(f"Ошибка воркера: {exc}")
            self.log(traceback.format_exc())
        finally:
            self.finished_signal.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Instagram Android Bot — RegerFIZ")
        self.resize(980, 720)

        self.worker = None
        self.success_count = 0
        self.failed_count = 0
        self._device_check_state = {}
        self._device_root_state = {}

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        header = QLabel("🤖 Instagram Android Registration")
        header.setStyleSheet("font-size: 18px; font-weight: 700; color: #ffb347;")
        sub_header = QLabel("uiautomator2 · Samsung · English UI")
        sub_header.setStyleSheet("color: #888; margin-bottom: 8px;")
        main_layout.addWidget(header)
        main_layout.addWidget(sub_header)

        devices_group = QGroupBox("📱 Подключённые устройства (ADB)")
        devices_layout = QVBoxLayout()
        devices_group.setLayout(devices_layout)

        devices_hint = QLabel(
            "Список обновляется автоматически. Отключённые устройства исчезают. "
            "Отметьте галочкой, с какими работать."
        )
        devices_hint.setStyleSheet("color: #888; font-size: 10px;")
        devices_layout.addWidget(devices_hint)

        self.connected_devices_label = QLabel("Подключено устройств: 0 | Отмечено: 0")
        self.connected_devices_label.setStyleSheet(
            "color: #ffb347; font-size: 11px; font-weight: 600;"
        )
        devices_layout.addWidget(self.connected_devices_label)

        self.devices_table = QTableWidget()
        self.devices_table.setColumnCount(5)
        self.devices_table.setHorizontalHeaderLabels(
            ["", "Serial", "Модель", "Подключение", "Root"]
        )
        self.devices_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.devices_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.devices_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.devices_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.devices_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.devices_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.devices_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.devices_table.setAlternatingRowColors(True)
        self.devices_table.verticalHeader().setVisible(False)
        self.devices_table.setMinimumHeight(140)
        devices_layout.addWidget(self.devices_table)

        dev_buttons = QHBoxLayout()
        self.refresh_devices_btn = QPushButton("🔄 Обновить")
        self.refresh_devices_btn.clicked.connect(self.refresh_devices)
        self.select_all_devices_btn = QPushButton("✅ Все")
        self.select_all_devices_btn.clicked.connect(self.select_all_devices)
        self.deselect_all_devices_btn = QPushButton("⬜ Снять")
        self.deselect_all_devices_btn.clicked.connect(self.deselect_all_devices)
        self.root_all_devices_btn = QPushButton("🔄 Root на всех")
        self.root_all_devices_btn.setToolTip(
            "Device Emulator (Randomise All) перед установкой Instagram на всех телефонах"
        )
        self.root_all_devices_btn.clicked.connect(self.enable_root_all_devices)
        self.root_none_devices_btn = QPushButton("⬜ Снять Root")
        self.root_none_devices_btn.setToolTip("Отключить Root на всех телефонах в списке")
        self.root_none_devices_btn.clicked.connect(self.disable_root_all_devices)
        dev_buttons.addWidget(self.refresh_devices_btn)
        dev_buttons.addWidget(self.select_all_devices_btn)
        dev_buttons.addWidget(self.deselect_all_devices_btn)
        dev_buttons.addWidget(self.root_all_devices_btn)
        dev_buttons.addWidget(self.root_none_devices_btn)
        dev_buttons.addStretch()
        devices_layout.addLayout(dev_buttons)
        main_layout.addWidget(devices_group)

        settings_group = QGroupBox("⚙️ Настройки")
        settings_layout = QVBoxLayout()
        settings_group.setLayout(settings_layout)

        acc_layout = QHBoxLayout()
        acc_layout.addWidget(QLabel("Количество аккаунтов:"))
        self.accounts_count_spin = QSpinBox()
        self.accounts_count_spin.setRange(1, 1000)
        self.accounts_count_spin.setValue(1)
        acc_layout.addWidget(self.accounts_count_spin)
        acc_layout.addStretch()
        settings_layout.addLayout(acc_layout)

        retries_layout = QHBoxLayout()
        retries_layout.addWidget(QLabel("Попыток на аккаунт (MAX_RETRIES):"))
        self.max_retries_spin = QSpinBox()
        self.max_retries_spin.setRange(1, 10)
        self.max_retries_spin.setValue(3)
        retries_layout.addWidget(self.max_retries_spin)
        retries_layout.addStretch()
        settings_layout.addLayout(retries_layout)

        install_layout = QHBoxLayout()
        install_layout.addWidget(QLabel("Установка Instagram:"))
        self.install_method_combo = QComboBox()
        self.install_method_combo.addItem("Play Маркет", "play_store")
        self.install_method_combo.addItem("APK / APKM (папка apk)", "apkm")
        install_layout.addWidget(self.install_method_combo)
        install_layout.addStretch()
        settings_layout.addLayout(install_layout)

        install_hint = QLabel(
            "APKM: положите com.instagram.apkm в папку apk рядом с софтом "
            "(или com.instagram.apk для одного файла)"
        )
        install_hint.setStyleSheet("color: #888; font-size: 10px;")
        install_hint.setWordWrap(True)
        settings_layout.addWidget(install_hint)

        root_hint = QLabel(
            "Root (Device Emulator → Randomise All): включается отдельно для каждого "
            "телефона в таблице — колонка «Root»"
        )
        root_hint.setStyleSheet("color: #888; font-size: 10px;")
        root_hint.setWordWrap(True)
        settings_layout.addWidget(root_hint)

        email_mode_layout = QHBoxLayout()
        email_mode_layout.addWidget(QLabel("Сервис почты:"))
        self.email_mode_combo = QComboBox()
        self.email_mode_combo.addItem("AnyMessage", "anymessage")
        self.email_mode_combo.addItem("SMSBower", "smsbower")
        self.email_mode_combo.currentIndexChanged.connect(self.on_email_mode_changed)
        email_mode_layout.addWidget(self.email_mode_combo)
        email_mode_layout.addStretch()
        settings_layout.addLayout(email_mode_layout)

        domain_layout = QHBoxLayout()
        domain_layout.addWidget(QLabel("Домен почты:"))
        self.domain_combo = QComboBox()
        for d in DOMAINS:
            self.domain_combo.addItem(d, d)
        domain_layout.addWidget(self.domain_combo)
        domain_layout.addStretch()
        settings_layout.addLayout(domain_layout)

        any_layout = QHBoxLayout()
        any_layout.addWidget(QLabel("AnyMessage токен:"))
        self.anymessage_token_edit = QLineEdit()
        self.anymessage_token_edit.setEchoMode(QLineEdit.Password)
        self.anymessage_token_edit.setPlaceholderText("Токен AnyMessage API")
        any_layout.addWidget(self.anymessage_token_edit)
        settings_layout.addLayout(any_layout)

        sms_layout = QHBoxLayout()
        sms_layout.addWidget(QLabel("SMSBower токен:"))
        self.smsbower_token_edit = QLineEdit()
        self.smsbower_token_edit.setEchoMode(QLineEdit.Password)
        self.smsbower_token_edit.setPlaceholderText("Токен SMSBower API")
        sms_layout.addWidget(self.smsbower_token_edit)
        settings_layout.addLayout(sms_layout)

        main_layout.addWidget(settings_group)

        buttons_layout = QHBoxLayout()
        self.start_btn = QPushButton("🚀 Старт")
        self.stop_btn = QPushButton("🛑 Стоп")
        self.stop_btn.setEnabled(False)
        self.start_btn.clicked.connect(self.start_register)
        self.stop_btn.clicked.connect(self.stop_register)
        buttons_layout.addWidget(self.start_btn)
        buttons_layout.addWidget(self.stop_btn)
        main_layout.addLayout(buttons_layout)

        log_group = QGroupBox("📜 Лог")
        log_layout = QVBoxLayout()
        log_group.setLayout(log_layout)
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setStyleSheet(
            "background-color: #111318; color: #ff8266; font-family: Consolas, monospace;"
        )
        log_layout.addWidget(self.log_edit)
        main_layout.addWidget(log_group)

        self.setStyleSheet("""
            QWidget {
                background-color: #0f1115;
                color: #f0f0f0;
                font-family: "Segoe UI", Arial, sans-serif;
            }
            QGroupBox {
                border: 1px solid #ff6b00;
                margin-top: 12px;
                border-radius: 6px;
                background-color: #151821;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 6px;
                color: #ffb347;
                font-weight: 600;
            }
            QPushButton {
                background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ff6b00, stop:1 #ff2e63);
                color: white;
                border-radius: 4px;
                padding: 6px 14px;
                border: none;
                font-weight: 600;
            }
            QPushButton:disabled {
                background-color: #444;
            }
            QPushButton:hover:!disabled {
                background-color: #ff8533;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
            }
            QCheckBox::indicator:unchecked {
                border: 1px solid #555;
                background-color: #1f222b;
            }
            QCheckBox::indicator:checked {
                border: 1px solid #ff6b00;
                background-color: #ff6b00;
            }
            QSpinBox, QPlainTextEdit, QComboBox, QLineEdit, QTableWidget {
                background-color: #151821;
                border: 1px solid #333745;
                border-radius: 4px;
                padding: 4px;
            }
            QTableWidget {
                alternate-background-color: #1b1f2a;
                color: #f0f0f0;
                gridline-color: #333745;
            }
            QTableWidget::item {
                color: #f0f0f0;
                background-color: #151821;
            }
            QTableWidget::item:alternate {
                background-color: #1b1f2a;
            }
            QTableWidget::item:selected {
                background-color: #ff6b00;
                color: white;
            }
            QHeaderView::section {
                background-color: #0f1115;
                color: #ffb347;
                padding: 6px;
                border: 1px solid #333745;
                font-weight: 600;
            }
            QLabel {
                font-size: 11px;
            }
        """)

        self.devices_timer = QTimer(self)
        self.devices_timer.timeout.connect(self.refresh_devices)
        self.devices_timer.start(3000)

        self._load_config()
        self.on_email_mode_changed()
        self.refresh_devices()

    def append_log(self, text):
        self.log_edit.appendPlainText(str(text))
        self.log_edit.verticalScrollBar().setValue(
            self.log_edit.verticalScrollBar().maximum()
        )

    def on_email_mode_changed(self):
        mode = self.email_mode_combo.currentData()
        is_any = mode == "anymessage"
        is_sms = mode == "smsbower"
        self.anymessage_token_edit.setEnabled(is_any)
        self.smsbower_token_edit.setEnabled(is_sms)

    def _save_device_table_state(self):
        for row in range(self.devices_table.rowCount()):
            check_item = self.devices_table.item(row, 0)
            serial_item = self.devices_table.item(row, 1)
            root_item = self.devices_table.item(row, 4)
            if not serial_item:
                continue
            serial = serial_item.text()
            if check_item:
                self._device_check_state[serial] = check_item.checkState() == Qt.Checked
            if root_item:
                self._device_root_state[serial] = root_item.checkState() == Qt.Checked

    def _save_device_check_state(self):
        self._save_device_table_state()

    def _get_device_root_map(self):
        self._save_device_table_state()
        result = {}
        for row in range(self.devices_table.rowCount()):
            serial_item = self.devices_table.item(row, 1)
            if not serial_item:
                continue
            serial = serial_item.text()
            result[serial] = self._device_root_state.get(serial, False)
        return result

    def _get_checked_devices(self):
        selected = []
        for row in range(self.devices_table.rowCount()):
            item = self.devices_table.item(row, 0)
            serial_item = self.devices_table.item(row, 1)
            if not item or not serial_item:
                continue
            if item.checkState() == Qt.Checked:
                selected.append(serial_item.text())
        return selected

    def _update_devices_count_label(self):
        connected = self.devices_table.rowCount()
        checked_count = sum(
            1
            for row in range(connected)
            if self.devices_table.item(row, 0)
            and self.devices_table.item(row, 0).checkState() == Qt.Checked
        )
        self.connected_devices_label.setText(
            f"Подключено устройств: {connected} | Отмечено: {checked_count}"
        )

    def refresh_devices(self):
        self._save_device_table_state()
        connected = get_connected_devices()

        for serial in list(self._device_check_state.keys()):
            if serial not in connected:
                del self._device_check_state[serial]
        for serial in list(self._device_root_state.keys()):
            if serial not in connected:
                del self._device_root_state[serial]

        self.devices_table.setRowCount(len(connected))
        for row, (serial, model) in enumerate(sorted(connected.items())):
            check_item = QTableWidgetItem()
            check_item.setFlags(
                Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable
            )
            checked = self._device_check_state.get(serial, serial in self._saved_selected_devices)
            check_item.setCheckState(Qt.Checked if checked else Qt.Unchecked)

            serial_item = QTableWidgetItem(serial)
            model_item = QTableWidgetItem(model)
            connection_item = QTableWidgetItem(
                "Wi-Fi" if _is_wifi_adb_serial(serial) else "USB"
            )

            root_item = QTableWidgetItem()
            root_item.setFlags(
                Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable
            )
            root_item.setToolTip(
                "Root: Device Emulator → Randomise All перед установкой Instagram"
            )
            root_on = self._device_root_state.get(
                serial,
                self._saved_device_root.get(serial, False),
            )
            root_item.setCheckState(Qt.Checked if root_on else Qt.Unchecked)

            self.devices_table.setItem(row, 0, check_item)
            self.devices_table.setItem(row, 1, serial_item)
            self.devices_table.setItem(row, 2, model_item)
            self.devices_table.setItem(row, 3, connection_item)
            self.devices_table.setItem(row, 4, root_item)

        self._update_devices_count_label()

    def select_all_devices(self):
        for row in range(self.devices_table.rowCount()):
            item = self.devices_table.item(row, 0)
            if item:
                item.setCheckState(Qt.Checked)
        self._update_devices_count_label()

    def deselect_all_devices(self):
        for row in range(self.devices_table.rowCount()):
            item = self.devices_table.item(row, 0)
            if item:
                item.setCheckState(Qt.Unchecked)
        self._update_devices_count_label()

    def _set_root_all_devices(self, enabled):
        state = Qt.Checked if enabled else Qt.Unchecked
        for row in range(self.devices_table.rowCount()):
            root_item = self.devices_table.item(row, 4)
            serial_item = self.devices_table.item(row, 1)
            if root_item:
                root_item.setCheckState(state)
            if serial_item:
                self._device_root_state[serial_item.text()] = enabled

    def enable_root_all_devices(self):
        self._set_root_all_devices(True)

    def disable_root_all_devices(self):
        self._set_root_all_devices(False)

    def start_register(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "Занято", "Процесс уже запущен")
            return

        self._save_device_table_state()
        devices = self._get_checked_devices()
        if not devices:
            QMessageBox.warning(
                self,
                "Нет устройств",
                "Подключите устройство через ADB и отметьте его галочкой.",
            )
            return

        email_mode = self.email_mode_combo.currentData()
        anymessage_token = self.anymessage_token_edit.text().strip()
        smsbower_token = self.smsbower_token_edit.text().strip()

        if email_mode == "anymessage" and not anymessage_token:
            QMessageBox.warning(self, "Ошибка", "Введите AnyMessage токен")
            return
        if email_mode == "smsbower" and not smsbower_token:
            QMessageBox.warning(self, "Ошибка", "Введите SMSBower токен")
            return

        self.success_count = 0
        self.failed_count = 0

        self.worker = RegisterWorker(
            accounts_count=self.accounts_count_spin.value(),
            devices=devices,
            email_domain=self.domain_combo.currentData(),
            email_mode=email_mode,
            anymessage_token=anymessage_token,
            smsbower_token=smsbower_token,
            max_retries=self.max_retries_spin.value(),
            install_method=self.install_method_combo.currentData(),
            device_root=self._get_device_root_map(),
        )
        self.worker.log_signal.connect(self.append_log)
        self.worker.account_result_signal.connect(self.on_account_result)
        self.worker.finished_signal.connect(self.on_worker_finished)
        self.worker.start()

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.append_log("=== Старт регистрации ===")
        self._save_config()

    def stop_register(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.append_log("Запрошена остановка...")
        else:
            self.append_log("Процесс не запущен")

    def on_account_result(self, data):
        if not isinstance(data, dict):
            return
        if data.get("success"):
            self.success_count += 1
        else:
            self.failed_count += 1

    def on_worker_finished(self):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.append_log(f"Итог: успешно {self.success_count}, неуспешно {self.failed_count}")
        self.append_log("=== Процесс завершён ===")

    def _config_path(self):
        return Path(__file__).parent / CONFIG_FILE

    def _load_config(self):
        self._saved_selected_devices = set()
        self._saved_device_root = {}
        try:
            path = self._config_path()
            if not path.exists():
                return
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            self.accounts_count_spin.setValue(data.get("accounts_count", 1))
            self.max_retries_spin.setValue(data.get("max_retries", 3))

            install_method = data.get("install_method", "play_store")
            idx = self.install_method_combo.findData(install_method)
            if idx >= 0:
                self.install_method_combo.setCurrentIndex(idx)

            device_root = data.get("device_root", {})
            if isinstance(device_root, dict):
                self._saved_device_root = {
                    str(k): bool(v) for k, v in device_root.items()
                }
            elif data.get("root_enabled"):
                # Старый формат: один Root на всех — переносим на выбранные устройства
                selected = data.get("selected_devices", [])
                self._saved_device_root = {str(s): True for s in selected}

            email_mode = data.get("email_mode", "anymessage")
            idx = self.email_mode_combo.findData(email_mode)
            if idx >= 0:
                self.email_mode_combo.setCurrentIndex(idx)

            email_domain = data.get("email_domain", "outlook.com")
            idx = self.domain_combo.findData(email_domain)
            if idx >= 0:
                self.domain_combo.setCurrentIndex(idx)

            self.anymessage_token_edit.setText(data.get("anymessage_token", ""))
            self.smsbower_token_edit.setText(data.get("smsbower_token", ""))

            selected = data.get("selected_devices", [])
            self._saved_selected_devices = set(selected) if selected else set()
            for serial in selected:
                self._device_check_state[serial] = True
        except Exception:
            pass

    def _save_config(self):
        try:
            self._save_device_table_state()
            selected = self._get_checked_devices()
            data = {
                "accounts_count": self.accounts_count_spin.value(),
                "max_retries": self.max_retries_spin.value(),
                "install_method": self.install_method_combo.currentData(),
                "device_root": self._get_device_root_map(),
                "email_mode": self.email_mode_combo.currentData(),
                "email_domain": self.domain_combo.currentData(),
                "anymessage_token": self.anymessage_token_edit.text().strip(),
                "smsbower_token": self.smsbower_token_edit.text().strip(),
                "selected_devices": selected,
            }
            with self._config_path().open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def closeEvent(self, event):
        self._save_config()
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(3000)
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
