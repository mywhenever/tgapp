import asyncio
import json
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.tl.functions.channels import CreateChannelRequest, EditPhotoRequest, InviteToChannelRequest
from telethon.tl.functions.contacts import ImportContactsRequest
from telethon.tl.types import InputChatUploadedPhoto, InputPhoneContact

SETTINGS_FILE = Path("app_settings.json")


@dataclass
class ContactData:
    first_name: str
    last_name: str
    phone: str


class WorkerSignals(QObject):
    success = Signal(object)
    error = Signal(str)
    done = Signal()


class AsyncTask(QRunnable):
    def __init__(self, fn: Callable[[], object]) -> None:
        super().__init__()
        self.fn = fn
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            result = self.fn()
            self.signals.success.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.signals.error.emit(str(exc))
        finally:
            self.signals.done.emit()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Telegram Helper")
        self.resize(1000, 840)
        self._apply_styles()

        self.thread_pool = QThreadPool.globalInstance()
        self.phone_code_hash: Optional[str] = None
        self._loading_settings = False

        # global app credentials (API ID + API HASH) used for login and all operations
        self.api_id_input = QLineEdit()
        self.api_id_input.setPlaceholderText("API ID")
        self.api_hash_input = QLineEdit()
        self.api_hash_input.setPlaceholderText("API HASH")
        self.btn_save_api = QPushButton("Сохранить API пару")

        # Telegram accounts menu (no API pair inside)
        self.accounts: list[dict[str, str]] = []
        self.account_combo = QComboBox()
        self.account_combo.setEditable(False)

        self.account_name_input = QLineEdit()
        self.account_name_input.setPlaceholderText("Например: Личный")
        self.account_phone_input = QLineEdit()
        self.account_phone_input.setPlaceholderText("+79990001122")
        self.account_session_input = QLineEdit()
        self.account_session_input.setPlaceholderText("session name, например: personal")

        self.btn_add_account = QPushButton("Добавить / обновить аккаунт")
        self.btn_remove_account = QPushButton("Удалить аккаунт")
        self.btn_save_accounts = QPushButton("Сохранить меню аккаунтов")
        self.btn_use_account = QPushButton("Выбрать аккаунт")
        self.btn_clear_account = QPushButton("Очистить форму")

        # login section
        self.phone_input = QLineEdit()
        self.phone_input.setPlaceholderText("Телефон для входа")
        self.code_input = QLineEdit()
        self.code_input.setPlaceholderText("Код из Telegram")
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Если включён 2FA")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.btn_request_code = QPushButton("Отправить код")
        self.btn_sign_in = QPushButton("Войти в аккаунт")

        # common
        self.auth_delay_spin = QSpinBox()
        self.auth_delay_spin.setRange(0, 120)
        self.auth_delay_spin.setSuffix(" сек")

        # contacts delay
        self.contacts_delay_min_spin = QSpinBox()
        self.contacts_delay_min_spin.setRange(0, 120)
        self.contacts_delay_min_spin.setSuffix(" сек")
        self.contacts_delay_max_spin = QSpinBox()
        self.contacts_delay_max_spin.setRange(0, 120)
        self.contacts_delay_max_spin.setValue(3)
        self.contacts_delay_max_spin.setSuffix(" сек")
        self.contacts_random_delay_checkbox = QCheckBox("Рандомная задержка")
        self.contacts_random_delay_checkbox.setChecked(True)

        # groups delay
        self.groups_delay_min_spin = QSpinBox()
        self.groups_delay_min_spin.setRange(0, 120)
        self.groups_delay_min_spin.setValue(1)
        self.groups_delay_min_spin.setSuffix(" сек")
        self.groups_delay_max_spin = QSpinBox()
        self.groups_delay_max_spin.setRange(0, 120)
        self.groups_delay_max_spin.setValue(5)
        self.groups_delay_max_spin.setSuffix(" сек")
        self.groups_random_delay_checkbox = QCheckBox("Рандомная задержка")
        self.groups_random_delay_checkbox.setChecked(True)

        # groups
        self.group_title_input = QLineEdit("Новая супергруппа")
        self.group_about_input = QLineEdit("Создано через Telethon")
        self.group_count_spin = QSpinBox()
        self.group_count_spin.setRange(1, 100)
        self.group_count_spin.setValue(1)
        self.photo_path_input = QLineEdit()
        self.photo_path_input.setPlaceholderText("Фото опционально")
        self.btn_pick_photo = QPushButton("Выбрать фото")
        self.use_members_checkbox = QCheckBox("Добавлять участников")
        self.use_members_checkbox.setChecked(True)
        self.btn_create_with_members = QPushButton("Создать группы + участники")
        self.btn_create_without_members = QPushButton("Создать группы без участников")

        # contacts
        self.contacts_input = QPlainTextEdit()
        self.contacts_input.setPlainText(
            "Бондаренко Ирина Петровна 20.08.1970\n"
            "https://t.me/+79219710241\n\n"
            "Бондаренко Марина Васильевна 13.02.1979\n"
            "https://t.me/+79643327643\n"
        )
        self.usernames_input = QPlainTextEdit()
        self.usernames_input.setPlaceholderText("@durov\ntelegram\nhttps://t.me/example_username")
        self.user_ids_input = QPlainTextEdit()
        self.user_ids_input.setPlaceholderText("123456789\n987654321")
        self.btn_add_users_only = QPushButton("Проверить и добавить пользователей")

        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)

        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.TabPosition.South)
        self.tabs.setDocumentMode(True)
        self.page_accounts = QWidget()
        self.page_groups = QWidget()
        self.page_contacts = QWidget()

        self._build_tabs()
        self._bind_events()
        self._setup_ui_hints()
        self._load_settings()

    def _setup_ui_hints(self) -> None:
        self.statusBar().showMessage("Готово к работе")

        self.contacts_input.setPlaceholderText(
            "Иванов Иван Иванович 01.01.1990\nhttps://t.me/+79990001122\n\nПетров Петр 02.02.1992\nhttps://t.me/+79990001123"
        )
        self.usernames_input.setPlaceholderText("@durov\ntelegram\nhttps://t.me/example_username")

        self.auth_delay_spin.setToolTip("Пауза перед отправкой кода и входом")
        self.contacts_delay_min_spin.setToolTip("Минимальная пауза перед обработкой контактов")
        self.contacts_delay_max_spin.setToolTip("Максимальная пауза, если включён рандом")
        self.groups_delay_min_spin.setToolTip("Минимальная пауза перед созданием/действиями в группах")
        self.groups_delay_max_spin.setToolTip("Максимальная пауза, если включён рандом")

        self._toggle_delay_max(self.contacts_random_delay_checkbox, self.contacts_delay_min_spin, self.contacts_delay_max_spin)
        self._toggle_delay_max(self.groups_random_delay_checkbox, self.groups_delay_min_spin, self.groups_delay_max_spin)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow { background: #f6f8fb; }
            QGroupBox { background: #fff; border: 1px solid #dce3ef; border-radius: 10px; margin-top: 10px; font-weight: 600; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QLineEdit, QComboBox, QSpinBox, QPlainTextEdit { background:#fff; border:1px solid #cfd8e6; border-radius:8px; padding:6px; }
            QPushButton { background:#2f6df6; color:#fff; border:none; border-radius:8px; padding:8px 12px; font-weight:600; }
            QPushButton:hover { background:#255ede; }
            QPushButton:disabled { background:#a7b7dd; }
            QTabWidget::pane { border: 1px solid #dce3ef; border-radius: 10px; background:#f6f8fb; }
            QTabBar::tab { background:#e6ecfa; min-width:130px; padding:8px 14px; margin:4px; border-radius:10px; border:1px solid #cdd8f0; }
            QTabBar::tab:selected { background:#2f6df6; color:#fff; border-color:#2f6df6; }
            """
        )

    def _build_tabs(self) -> None:
        self._build_accounts_page()
        self._build_groups_page()
        self._build_contacts_page()
        self.tabs.addTab(self.page_accounts, "Аккаунты")
        self.tabs.addTab(self.page_groups, "Группы")
        self.tabs.addTab(self.page_contacts, "Контакты")
        self.tabs.currentChanged.connect(lambda i: self.statusBar().showMessage(f"Раздел: {self.tabs.tabText(i)}"))
        self.setCentralWidget(self.tabs)

    def _build_accounts_page(self) -> None:
        layout = QVBoxLayout(self.page_accounts)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        api_box = QGroupBox("API для входа (используется везде после входа)")
        api_form = QFormLayout(api_box)
        api_form.addRow("API ID", self.api_id_input)
        api_form.addRow("API HASH", self.api_hash_input)
        api_form.addRow("", self.btn_save_api)
        layout.addWidget(api_box)

        menu_box = QGroupBox("Меню аккаунтов Telegram")
        menu_form = QFormLayout(menu_box)
        row = QWidget()
        row_l = QHBoxLayout(row)
        row_l.setContentsMargins(0, 0, 0, 0)
        row_l.addWidget(self.account_combo)
        row_l.addWidget(self.btn_use_account)
        row_l.addWidget(self.btn_remove_account)
        row_l.addWidget(self.btn_save_accounts)
        menu_form.addRow("Выбор аккаунта", row)

        menu_form.addRow("Название", self.account_name_input)
        menu_form.addRow("Телефон", self.account_phone_input)
        menu_form.addRow("Session", self.account_session_input)

        btn_row = QWidget()
        btn_row_l = QHBoxLayout(btn_row)
        btn_row_l.setContentsMargins(0, 0, 0, 0)
        btn_row_l.addWidget(self.btn_add_account)
        btn_row_l.addWidget(self.btn_clear_account)
        menu_form.addRow("", btn_row)
        layout.addWidget(menu_box)

        login_box = QGroupBox("Вход в аккаунт")
        login_form = QFormLayout(login_box)
        login_form.addRow("", QLabel("1) Отправьте код  2) Введите код  3) Нажмите «Войти»"))
        login_form.addRow("Телефон для входа", self.phone_input)

        code_row = QWidget()
        code_row_l = QHBoxLayout(code_row)
        code_row_l.setContentsMargins(0, 0, 0, 0)
        code_row_l.addWidget(self.code_input)
        code_row_l.addWidget(self.btn_request_code)
        code_row_l.addWidget(self.btn_sign_in)
        login_form.addRow("Код", code_row)
        login_form.addRow("2FA пароль", self.password_input)
        login_form.addRow("Задержка", self.auth_delay_spin)
        layout.addWidget(login_box)

        log_box = QGroupBox("Лог")
        log_l = QVBoxLayout(log_box)
        log_l.addWidget(self.log_output)
        layout.addWidget(log_box, stretch=1)

    def _build_groups_page(self) -> None:
        layout = QVBoxLayout(self.page_groups)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        box = QGroupBox("Параметры групп")
        form = QFormLayout(box)
        form.addRow("Название", self.group_title_input)
        form.addRow("Описание", self.group_about_input)
        form.addRow("Количество групп", self.group_count_spin)
        photo_row = QWidget()
        photo_l = QHBoxLayout(photo_row)
        photo_l.setContentsMargins(0, 0, 0, 0)
        photo_l.addWidget(self.photo_path_input)
        photo_l.addWidget(self.btn_pick_photo)
        form.addRow("Фото", photo_row)
        form.addRow("Участники", self.use_members_checkbox)

        delay_row = QWidget()
        delay_row_l = QHBoxLayout(delay_row)
        delay_row_l.setContentsMargins(0, 0, 0, 0)
        delay_row_l.addWidget(QLabel("от"))
        delay_row_l.addWidget(self.groups_delay_min_spin)
        delay_row_l.addWidget(QLabel("до"))
        delay_row_l.addWidget(self.groups_delay_max_spin)
        delay_row_l.addWidget(self.groups_random_delay_checkbox)
        form.addRow("Задержка действий", delay_row)
        form.addRow("", QLabel("Если рандом включён, пауза берётся случайно между от/до."))
        layout.addWidget(box)

        btn_row = QWidget()
        btn_l = QHBoxLayout(btn_row)
        btn_l.setContentsMargins(0, 0, 0, 0)
        btn_l.addWidget(self.btn_create_with_members)
        btn_l.addWidget(self.btn_create_without_members)
        layout.addWidget(btn_row)
        layout.addStretch(1)

    def _build_contacts_page(self) -> None:
        layout = QVBoxLayout(self.page_contacts)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        contacts_box = QGroupBox("Контакты")
        c_l = QVBoxLayout(contacts_box)
        c_l.addWidget(QLabel("ФИО + ссылка t.me/+7... блоками"))
        self.contacts_input.setFixedHeight(220)
        c_l.addWidget(self.contacts_input)
        layout.addWidget(contacts_box)

        refs_box = QGroupBox("Юзернеймы и ссылки")
        r_l = QVBoxLayout(refs_box)
        self.usernames_input.setFixedHeight(120)
        r_l.addWidget(self.usernames_input)
        layout.addWidget(refs_box)

        ids_box = QGroupBox("ID пользователей")
        i_l = QVBoxLayout(ids_box)
        self.user_ids_input.setFixedHeight(90)
        i_l.addWidget(self.user_ids_input)
        i_l.addWidget(QLabel("Можно оставить пустым, если хотите работать только с контактами/username."))

        delay_row = QWidget()
        delay_row_l = QHBoxLayout(delay_row)
        delay_row_l.setContentsMargins(0, 0, 0, 0)
        delay_row_l.addWidget(QLabel("от"))
        delay_row_l.addWidget(self.contacts_delay_min_spin)
        delay_row_l.addWidget(QLabel("до"))
        delay_row_l.addWidget(self.contacts_delay_max_spin)
        delay_row_l.addWidget(self.contacts_random_delay_checkbox)
        i_l.addWidget(QLabel("Задержка обработки контактов"))
        i_l.addWidget(delay_row)
        i_l.addWidget(self.btn_add_users_only)
        layout.addWidget(ids_box)

    @staticmethod
    def _to_int(value: object, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_bool(value: object, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on", "да"}:
                return True
            if normalized in {"0", "false", "no", "off", "нет"}:
                return False
        if isinstance(value, (int, float)):
            return bool(value)
        return default

    def _on_delay_controls_changed(self) -> None:
        if self._loading_settings:
            return
        self._save_settings()

    def _bind_events(self) -> None:
        self.btn_save_api.clicked.connect(self.save_api_pair)

        self.btn_add_account.clicked.connect(self.add_or_update_account)
        self.btn_remove_account.clicked.connect(self.remove_account)
        self.btn_save_accounts.clicked.connect(self.save_accounts)
        self.btn_use_account.clicked.connect(self.apply_selected_account)
        self.btn_clear_account.clicked.connect(self.clear_account_editor)
        self.account_combo.currentIndexChanged.connect(self.on_account_selected)

        self.btn_request_code.clicked.connect(self.request_code)
        self.btn_sign_in.clicked.connect(self.sign_in)
        self.btn_pick_photo.clicked.connect(self.pick_photo)
        self.btn_create_with_members.clicked.connect(lambda: self.create_groups(add_members=True))
        self.btn_create_without_members.clicked.connect(lambda: self.create_groups(add_members=False))
        self.btn_add_users_only.clicked.connect(self.add_users_without_groups)

        self.contacts_random_delay_checkbox.toggled.connect(
            lambda _: self._toggle_delay_max(self.contacts_random_delay_checkbox, self.contacts_delay_min_spin, self.contacts_delay_max_spin)
        )
        self.groups_random_delay_checkbox.toggled.connect(
            lambda _: self._toggle_delay_max(self.groups_random_delay_checkbox, self.groups_delay_min_spin, self.groups_delay_max_spin)
        )
        self.contacts_delay_min_spin.valueChanged.connect(
            lambda _: self._normalize_delay_range(self.contacts_delay_min_spin, self.contacts_delay_max_spin)
        )
        self.groups_delay_min_spin.valueChanged.connect(
            lambda _: self._normalize_delay_range(self.groups_delay_min_spin, self.groups_delay_max_spin)
        )
        self.contacts_delay_max_spin.valueChanged.connect(
            lambda _: self._normalize_delay_range_reverse(self.contacts_delay_min_spin, self.contacts_delay_max_spin)
        )
        self.groups_delay_max_spin.valueChanged.connect(
            lambda _: self._normalize_delay_range_reverse(self.groups_delay_min_spin, self.groups_delay_max_spin)
        )

        self.auth_delay_spin.valueChanged.connect(lambda _: self._on_delay_controls_changed())
        self.contacts_delay_min_spin.valueChanged.connect(lambda _: self._on_delay_controls_changed())
        self.contacts_delay_max_spin.valueChanged.connect(lambda _: self._on_delay_controls_changed())
        self.contacts_random_delay_checkbox.toggled.connect(lambda _: self._on_delay_controls_changed())
        self.groups_delay_min_spin.valueChanged.connect(lambda _: self._on_delay_controls_changed())
        self.groups_delay_max_spin.valueChanged.connect(lambda _: self._on_delay_controls_changed())
        self.groups_random_delay_checkbox.toggled.connect(lambda _: self._on_delay_controls_changed())

    def _load_settings(self) -> None:
        data = {}
        if SETTINGS_FILE.exists():
            try:
                data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            except Exception:
                data = {}

        api = data.get("api", {}) if isinstance(data, dict) else {}
        self.api_id_input.setText(str(api.get("api_id", "")).strip())
        self.api_hash_input.setText(str(api.get("api_hash", "")).strip())

        accounts_raw = data.get("accounts", []) if isinstance(data, dict) else []
        self.accounts = []
        for a in accounts_raw:
            if not isinstance(a, dict):
                continue
            name = str(a.get("name", "")).strip()
            phone = str(a.get("phone", "")).strip()
            session = str(a.get("session", "")).strip()
            if phone and session:
                self.accounts.append({"name": name or phone, "phone": phone, "session": session})

        delays = data.get("delays", {}) if isinstance(data, dict) else {}
        self._loading_settings = True
        self.auth_delay_spin.setValue(self._to_int(delays.get("auth"), self.auth_delay_spin.value()))
        self.contacts_delay_min_spin.setValue(self._to_int(delays.get("contacts_min"), self.contacts_delay_min_spin.value()))
        self.contacts_delay_max_spin.setValue(self._to_int(delays.get("contacts_max"), self.contacts_delay_max_spin.value()))
        self.contacts_random_delay_checkbox.setChecked(
            self._to_bool(delays.get("contacts_random"), self.contacts_random_delay_checkbox.isChecked())
        )
        self.groups_delay_min_spin.setValue(self._to_int(delays.get("groups_min"), self.groups_delay_min_spin.value()))
        self.groups_delay_max_spin.setValue(self._to_int(delays.get("groups_max"), self.groups_delay_max_spin.value()))
        self.groups_random_delay_checkbox.setChecked(
            self._to_bool(delays.get("groups_random"), self.groups_random_delay_checkbox.isChecked())
        )

        self._refresh_accounts_combo()
        self._normalize_delay_range(self.contacts_delay_min_spin, self.contacts_delay_max_spin)
        self._normalize_delay_range(self.groups_delay_min_spin, self.groups_delay_max_spin)
        self._toggle_delay_max(self.contacts_random_delay_checkbox, self.contacts_delay_min_spin, self.contacts_delay_max_spin)
        self._toggle_delay_max(self.groups_random_delay_checkbox, self.groups_delay_min_spin, self.groups_delay_max_spin)
        self._loading_settings = False

    def _save_settings(self) -> None:
        payload = {
            "api": {
                "api_id": self.api_id_input.text().strip(),
                "api_hash": self.api_hash_input.text().strip(),
            },
            "accounts": self.accounts,
            "delays": {
                "auth": self.auth_delay_spin.value(),
                "contacts_min": self.contacts_delay_min_spin.value(),
                "contacts_max": self.contacts_delay_max_spin.value(),
                "contacts_random": self.contacts_random_delay_checkbox.isChecked(),
                "groups_min": self.groups_delay_min_spin.value(),
                "groups_max": self.groups_delay_max_spin.value(),
                "groups_random": self.groups_random_delay_checkbox.isChecked(),
            },
        }
        SETTINGS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_api_pair(self) -> None:
        api_id = self.api_id_input.text().strip()
        api_hash = self.api_hash_input.text().strip()
        if not api_id.isdigit():
            self.show_error("API ID должен быть числом")
            return
        if not api_hash:
            self.show_error("API HASH обязателен")
            return
        self._save_settings()
        self.log("API ID + API HASH сохранены и будут использоваться после входа")

    def _refresh_accounts_combo(self) -> None:
        self.account_combo.blockSignals(True)
        self.account_combo.clear()
        for acc in self.accounts:
            self.account_combo.addItem(f"{acc['name']} ({acc['phone']})", acc["session"])
        self.account_combo.blockSignals(False)
        if self.account_combo.count() > 0:
            self.account_combo.setCurrentIndex(0)
            self.on_account_selected()

    def on_account_selected(self) -> None:
        idx = self.account_combo.currentIndex()
        if idx < 0 or idx >= len(self.accounts):
            return
        acc = self.accounts[idx]
        self.account_name_input.setText(acc["name"])
        self.account_phone_input.setText(acc["phone"])
        self.account_session_input.setText(acc["session"])
        self.phone_input.setText(acc["phone"])

    def apply_selected_account(self) -> None:
        self.on_account_selected()
        idx = self.account_combo.currentIndex()
        if idx >= 0:
            self.log(f"Выбран аккаунт: {self.accounts[idx]['name']}")

    def clear_account_editor(self) -> None:
        self.account_name_input.clear()
        self.account_phone_input.clear()
        self.account_session_input.clear()

    def add_or_update_account(self) -> None:
        name = self.account_name_input.text().strip()
        phone = self.account_phone_input.text().strip()
        session = self.account_session_input.text().strip()
        if not phone or not session:
            self.show_error("Для аккаунта обязательны телефон и session")
            return
        if not name:
            name = phone

        updated = False
        for a in self.accounts:
            if a["session"] == session:
                a.update({"name": name, "phone": phone})
                updated = True
                break
        if not updated:
            self.accounts.append({"name": name, "phone": phone, "session": session})

        self._refresh_accounts_combo()
        self._save_settings()
        self.log(f"Аккаунт добавлен/обновлён: {name}")

    def remove_account(self) -> None:
        idx = self.account_combo.currentIndex()
        if idx < 0 or idx >= len(self.accounts):
            return
        name = self.accounts[idx]["name"]
        self.accounts.pop(idx)
        self._refresh_accounts_combo()
        self._save_settings()
        self.log(f"Аккаунт удалён: {name}")

    def save_accounts(self) -> None:
        self._save_settings()
        self.log(f"Меню аккаунтов сохранено ({len(self.accounts)} шт.)")

    def _client_from_fields(self) -> TelegramClient:
        api_id = self.api_id_input.text().strip()
        api_hash = self.api_hash_input.text().strip()
        if not api_id.isdigit() or not api_hash:
            raise ValueError("Сначала заполните API ID + API HASH")

        idx = self.account_combo.currentIndex()
        if idx < 0 or idx >= len(self.accounts):
            raise ValueError("Выберите аккаунт из меню аккаунтов")
        session = self.accounts[idx]["session"]
        return TelegramClient(session, int(api_id), api_hash)

    async def wait_auth_delay(self) -> None:
        delay = self.auth_delay_spin.value()
        if delay > 0:
            await asyncio.sleep(delay)

    @staticmethod
    def _get_delay(min_delay: int, max_delay: int, randomize: bool) -> int:
        left = min(min_delay, max_delay)
        right = max(min_delay, max_delay)
        if randomize:
            return random.randint(left, right)
        return left

    async def wait_contacts_delay(self) -> None:
        delay = self._get_delay(
            self.contacts_delay_min_spin.value(),
            self.contacts_delay_max_spin.value(),
            self.contacts_random_delay_checkbox.isChecked(),
        )
        if delay > 0:
            await asyncio.sleep(delay)

    async def wait_groups_delay(self) -> None:
        delay = self._get_delay(
            self.groups_delay_min_spin.value(),
            self.groups_delay_max_spin.value(),
            self.groups_random_delay_checkbox.isChecked(),
        )
        if delay > 0:
            await asyncio.sleep(delay)

    @staticmethod
    def _toggle_delay_max(random_checkbox: QCheckBox, min_spin: QSpinBox, max_spin: QSpinBox) -> None:
        is_random = random_checkbox.isChecked()
        max_spin.setEnabled(is_random)
        if not is_random:
            max_spin.setValue(min_spin.value())

    @staticmethod
    def _normalize_delay_range(min_spin: QSpinBox, max_spin: QSpinBox) -> None:
        if min_spin.value() > max_spin.value():
            max_spin.setValue(min_spin.value())

    @staticmethod
    def _normalize_delay_range_reverse(min_spin: QSpinBox, max_spin: QSpinBox) -> None:
        if max_spin.value() < min_spin.value():
            min_spin.setValue(max_spin.value())

    def run_task(self, fn: Callable[[], object], success_cb: Optional[Callable[[object], None]] = None) -> None:
        self.set_busy(True)
        self.statusBar().showMessage("Выполняется операция, пожалуйста подождите...")
        task = AsyncTask(fn)
        task.signals.success.connect(lambda result: success_cb(result) if success_cb else None)
        task.signals.error.connect(self.show_error)
        task.signals.done.connect(lambda: self.set_busy(False))
        task.signals.done.connect(lambda: self.statusBar().showMessage("Готово"))
        self.thread_pool.start(task)

    def set_busy(self, busy: bool) -> None:
        for btn in [
            self.btn_save_api,
            self.btn_add_account,
            self.btn_remove_account,
            self.btn_save_accounts,
            self.btn_use_account,
            self.btn_clear_account,
            self.btn_request_code,
            self.btn_sign_in,
            self.btn_pick_photo,
            self.btn_create_with_members,
            self.btn_create_without_members,
            self.btn_add_users_only,
        ]:
            btn.setEnabled(not busy)

    def log(self, text: str) -> None:
        self.log_output.appendPlainText(text)

    def show_error(self, text: str) -> None:
        self.log(f"[ОШИБКА] {text}")
        QMessageBox.critical(self, "Ошибка", text)

    @staticmethod
    def parse_contacts(raw: str) -> list[ContactData]:
        lines = [line.strip() for line in raw.splitlines()]
        contacts: list[ContactData] = []
        i = 0
        while i < len(lines):
            if not lines[i]:
                i += 1
                continue
            person_line = lines[i]
            if i + 1 >= len(lines):
                raise ValueError(f"Нет ссылки для строки: {person_line}")
            link_line = lines[i + 1]

            name_part = re.sub(r"\d{2}\.\d{2}\.\d{4}$", "", person_line).strip()
            tokens = [t for t in name_part.split() if t]
            if len(tokens) < 2:
                raise ValueError(f"Не удалось распознать ФИО: {person_line}")

            m = re.search(r"\+\d{10,15}", link_line)
            if not m:
                raise ValueError(f"Не удалось извлечь телефон: {link_line}")

            contacts.append(ContactData(first_name=tokens[1], last_name=tokens[0], phone=m.group(0)))
            i += 2
        return contacts

    @staticmethod
    def parse_user_refs(raw: str) -> list[str]:
        refs: list[str] = []
        for line in raw.splitlines():
            x = line.strip()
            if not x:
                continue
            if x.startswith("https://t.me/"):
                x = x.split("https://t.me/", 1)[1].strip("/")
            elif x.startswith("http://t.me/"):
                x = x.split("http://t.me/", 1)[1].strip("/")
            if x.startswith("+"):
                raise ValueError("Ссылки https://t.me/+... нельзя добавить напрямую")
            if x.startswith("@"):
                x = x[1:]
            if x:
                refs.append(x)
        return refs

    @staticmethod
    def parse_user_ids(raw: str) -> list[int]:
        ids: list[int] = []
        for line in raw.splitlines():
            v = line.strip()
            if not v:
                continue
            if not v.isdigit():
                raise ValueError(f"Некорректный ID пользователя: {v}")
            ids.append(int(v))
        return ids

    def pick_photo(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "Выберите фото", "", "Images (*.jpg *.jpeg *.png *.webp)")
        if p:
            self.photo_path_input.setText(p)

    def request_code(self) -> None:
        phone = self.phone_input.text().strip()
        if not phone:
            self.show_error("Введите телефон")
            return

        def job() -> str:
            async def _inner() -> str:
                client = self._client_from_fields()
                await client.connect()
                await self.wait_auth_delay()
                sent = await client.send_code_request(phone)
                await client.disconnect()
                return sent.phone_code_hash

            return asyncio.run(_inner())

        self.log("Отправка кода...")
        self.run_task(job, success_cb=lambda h: (setattr(self, "phone_code_hash", str(h)), self.log("Код отправлен")))

    def sign_in(self) -> None:
        phone = self.phone_input.text().strip()
        code = self.code_input.text().strip()
        pwd = self.password_input.text().strip()
        if not self.phone_code_hash:
            self.show_error("Сначала отправьте код")
            return

        def job() -> str:
            async def _inner() -> str:
                client = self._client_from_fields()
                await client.connect()
                await self.wait_auth_delay()
                try:
                    await client.sign_in(phone=phone, code=code, phone_code_hash=self.phone_code_hash)
                    return "Успешный вход в аккаунт"
                except SessionPasswordNeededError:
                    if not pwd:
                        raise ValueError("Нужен 2FA пароль")
                    await client.sign_in(password=pwd)
                    return "Успешный вход по 2FA"
                finally:
                    await client.disconnect()

            return asyncio.run(_inner())

        self.log("Выполняется вход...")
        self.run_task(job, success_cb=lambda msg: self.log(str(msg)))

    def add_users_without_groups(self) -> None:
        contacts_raw = self.contacts_input.toPlainText()
        refs_raw = self.usernames_input.toPlainText()
        ids_raw = self.user_ids_input.toPlainText()

        try:
            contacts = self.parse_contacts(contacts_raw) if contacts_raw.strip() else []
            refs = self.parse_user_refs(refs_raw) if refs_raw.strip() else []
            user_ids = self.parse_user_ids(ids_raw) if ids_raw.strip() else []
        except Exception as exc:  # noqa: BLE001
            self.show_error(str(exc))
            return

        def job() -> str:
            async def _inner() -> str:
                client = self._client_from_fields()
                await client.connect()
                if not await client.is_user_authorized():
                    await client.disconnect()
                    raise ValueError("Сначала выполните вход")

                logs = []
                if contacts:
                    await self.wait_contacts_delay()
                    batch = [InputPhoneContact(client_id=i + 1, phone=c.phone, first_name=c.first_name, last_name=c.last_name) for i, c in enumerate(contacts)]
                    imported = await client(ImportContactsRequest(batch))
                    logs.append(f"Импортировано контактов: {len(contacts)}")
                    logs.append(f"Telegram-профилей найдено: {len(imported.users)}")

                if refs:
                    ok, fail = 0, 0
                    for ref in refs:
                        await self.wait_contacts_delay()
                        try:
                            await client.get_entity(ref)
                            ok += 1
                        except Exception:
                            fail += 1
                    logs.append(f"Проверка username/ссылок: ok={ok}, fail={fail}")

                if user_ids:
                    ok_ids, fail_ids = 0, 0
                    for uid in user_ids:
                        await self.wait_contacts_delay()
                        try:
                            await client.get_entity(uid)
                            ok_ids += 1
                        except Exception:
                            fail_ids += 1
                    logs.append(f"Проверка user ID: ok={ok_ids}, fail={fail_ids}")

                await client.disconnect()
                return "\n".join(logs) if logs else "Нет данных для обработки"

            return asyncio.run(_inner())

        self.log("Запущена обработка пользователей...")
        self.run_task(job, success_cb=lambda result: self.log(str(result)))

    def create_groups(self, add_members: bool) -> None:
        title = self.group_title_input.text().strip()
        about = self.group_about_input.text().strip()
        cnt = self.group_count_spin.value()
        photo = self.photo_path_input.text().strip()

        if not title:
            self.show_error("Введите название группы")
            return

        add_members = add_members and self.use_members_checkbox.isChecked()
        try:
            contacts = self.parse_contacts(self.contacts_input.toPlainText()) if add_members else []
            refs = self.parse_user_refs(self.usernames_input.toPlainText()) if add_members else []
            user_ids = self.parse_user_ids(self.user_ids_input.toPlainText()) if add_members else []
        except Exception as exc:  # noqa: BLE001
            self.show_error(str(exc))
            return

        def job() -> str:
            async def _inner() -> str:
                client = self._client_from_fields()
                await client.connect()
                if not await client.is_user_authorized():
                    await client.disconnect()
                    raise ValueError("Сначала выполните вход")

                users_to_invite = []
                logs: list[str] = []
                if add_members:
                    if contacts:
                        await self.wait_contacts_delay()
                        batch = [InputPhoneContact(client_id=i + 1, phone=c.phone, first_name=c.first_name, last_name=c.last_name) for i, c in enumerate(contacts)]
                        imported = await client(ImportContactsRequest(batch))
                        users_to_invite.extend([u for u in imported.users if not getattr(u, "bot", False)])

                    for ref in refs:
                        await self.wait_contacts_delay()
                        try:
                            ent = await client.get_entity(ref)
                            if not getattr(ent, "bot", False):
                                users_to_invite.append(ent)
                        except Exception:
                            pass

                    for uid in user_ids:
                        await self.wait_contacts_delay()
                        try:
                            ent = await client.get_entity(uid)
                            if not getattr(ent, "bot", False):
                                users_to_invite.append(ent)
                        except Exception:
                            pass

                    uniq = {getattr(u, "id", None): u for u in users_to_invite if getattr(u, "id", None) is not None}
                    users_to_invite = list(uniq.values())
                    logs.append(f"Подготовлено участников: {len(users_to_invite)}")

                for i in range(cnt):
                    await self.wait_groups_delay()
                    gname = title if cnt == 1 else f"{title} #{i + 1}"
                    res = await client(CreateChannelRequest(title=gname, about=about, megagroup=True))
                    channel = res.chats[0]
                    logs.append(f"Создана группа: {gname}")

                    if photo:
                        p = Path(photo)
                        if not p.exists():
                            raise ValueError(f"Файл фото не найден: {photo}")
                        await self.wait_groups_delay()
                        uploaded = await client.upload_file(photo)
                        await client(EditPhotoRequest(channel=channel, photo=InputChatUploadedPhoto(uploaded)))

                    if add_members and users_to_invite:
                        await self.wait_groups_delay()
                        await client(InviteToChannelRequest(channel=channel, users=users_to_invite))
                        logs.append(f"Добавлено участников в {gname}: {len(users_to_invite)}")

                if not add_members:
                    logs.append("Группы созданы без участников")

                await client.disconnect()
                return "\n".join(logs)

            return asyncio.run(_inner())

        self.log("Запущено создание групп...")
        self.run_task(job, success_cb=lambda result: (self.log(str(result)), QMessageBox.information(self, "Готово", "Операция выполнена")))


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
