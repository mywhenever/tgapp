import asyncio
import json
import random
import re
import sys
import traceback
from collections import deque
from importlib import import_module
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

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

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 0.7


@dataclass
class ContactData:
    first_name: str
    last_name: str
    phone: str


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Telegram Helper")
        self.resize(1000, 840)
        self._apply_styles()

        # Очередь задач в UI-потоке.
        # Не запускаем операции параллельно, чтобы не ловить блокировки sqlite-сессии Telethon.
        self._task_queue: deque[tuple[Callable[[], object], Optional[Callable[[object], None]]]] = deque()
        self._task_in_progress = False
        self.phone_code_hash: Optional[str] = None
        self._auth_client: Optional[TelegramClient] = None
        self._auth_phone: str = ""
        self._loading_settings = False

        # global app credentials (API ID + API HASH) used for login and all operations
        self.api_id_input = QLineEdit()
        self.api_id_input.setPlaceholderText("API ID")
        self.api_hash_input = QLineEdit()
        self.api_hash_input.setPlaceholderText("API HASH")
        self.btn_save_api = QPushButton("Сохранить API пару")

        # Active Telegram account (single profile)
        self.account_phone_input = QLineEdit()
        self.account_phone_input.setPlaceholderText("+79990001122")
        self.btn_save_account = QPushButton("Сохранить текущий аккаунт")
        self.btn_change_account = QPushButton("Сменить аккаунт")

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
        self.group_type_combo = QComboBox()
        self.group_type_combo.addItem("Обычная супергруппа", "normal")
        self.group_type_combo.addItem("Группа с темами (форум)", "forum")
        self.topic_preset_combo = QComboBox()
        self.topic_preset_combo.addItem("Без дополнительной темы", "")
        self.topic_preset_combo.addItem("Общение", "Общение")
        self.topic_preset_combo.addItem("Вопросы и ответы", "Вопросы и ответы")
        self.topic_preset_combo.addItem("Новости", "Новости")
        self.topic_preset_combo.addItem("Поддержка", "Поддержка")
        self.topic_custom_input = QLineEdit()
        self.topic_custom_input.setPlaceholderText("Своя тема (опционально)")
        self.photo_path_input = QLineEdit()
        self.photo_path_input.setPlaceholderText("Фото опционально")
        self.btn_pick_photo = QPushButton("Выбрать фото")
        self.use_members_checkbox = QCheckBox("Добавлять участников")
        self.use_members_checkbox.setChecked(True)
        self.group_use_contacts_checkbox = QCheckBox("Контакты")
        self.group_use_contacts_checkbox.setChecked(True)
        self.group_use_usernames_checkbox = QCheckBox("Юзернеймы/ссылки")
        self.group_use_usernames_checkbox.setChecked(True)
        self.group_use_ids_checkbox = QCheckBox("ID пользователей")
        self.group_use_ids_checkbox.setChecked(False)
        self.group_contacts_input = QPlainTextEdit()
        self.group_contacts_input.setPlaceholderText(
            "Иванов Иван Иванович 01.01.1990\nhttps://t.me/+79990001122\n\nПетров Петр 02.02.1992\nhttps://t.me/+79990001123"
        )
        self.group_usernames_input = QPlainTextEdit()
        self.group_usernames_input.setPlaceholderText("@durov\ntelegram\nhttps://t.me/example_username")
        self.group_user_ids_input = QPlainTextEdit()
        self.group_user_ids_input.setPlaceholderText("123456789\n987654321")
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
        self.log_output.setMaximumBlockCount(0)

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

        self._sync_delay_controls(self.contacts_random_delay_checkbox, self.contacts_delay_min_spin, self.contacts_delay_max_spin)
        self._sync_delay_controls(self.groups_random_delay_checkbox, self.groups_delay_min_spin, self.groups_delay_max_spin)
        self._sync_forum_controls()
        self._sync_group_member_inputs()

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

        menu_box = QGroupBox("Текущий Telegram аккаунт")
        menu_form = QFormLayout(menu_box)
        menu_form.addRow("Телефон", self.account_phone_input)

        btn_row = QWidget()
        btn_row_l = QHBoxLayout(btn_row)
        btn_row_l.setContentsMargins(0, 0, 0, 0)
        btn_row_l.addWidget(self.btn_save_account)
        btn_row_l.addWidget(self.btn_change_account)
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
        form.addRow("Тип группы", self.group_type_combo)
        form.addRow("Тема (из списка)", self.topic_preset_combo)
        form.addRow("Тема (своя)", self.topic_custom_input)
        photo_row = QWidget()
        photo_l = QHBoxLayout(photo_row)
        photo_l.setContentsMargins(0, 0, 0, 0)
        photo_l.addWidget(self.photo_path_input)
        photo_l.addWidget(self.btn_pick_photo)
        form.addRow("Фото", photo_row)
        form.addRow("Участники", self.use_members_checkbox)

        member_types_row = QWidget()
        member_types_l = QHBoxLayout(member_types_row)
        member_types_l.setContentsMargins(0, 0, 0, 0)
        member_types_l.addWidget(self.group_use_contacts_checkbox)
        member_types_l.addWidget(self.group_use_usernames_checkbox)
        member_types_l.addWidget(self.group_use_ids_checkbox)
        member_types_l.addStretch(1)
        form.addRow("Кого добавлять", member_types_row)

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

        group_contacts_box = QGroupBox("Контакты для групп")
        group_contacts_l = QVBoxLayout(group_contacts_box)
        self.group_contacts_input.setFixedHeight(130)
        group_contacts_l.addWidget(self.group_contacts_input)
        layout.addWidget(group_contacts_box)

        group_refs_box = QGroupBox("Юзернеймы/ссылки для групп")
        group_refs_l = QVBoxLayout(group_refs_box)
        self.group_usernames_input.setFixedHeight(100)
        group_refs_l.addWidget(self.group_usernames_input)
        layout.addWidget(group_refs_box)

        group_ids_box = QGroupBox("ID пользователей для групп")
        group_ids_l = QVBoxLayout(group_ids_box)
        self.group_user_ids_input.setFixedHeight(80)
        group_ids_l.addWidget(self.group_user_ids_input)
        layout.addWidget(group_ids_box)

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

        self.btn_save_account.clicked.connect(self.save_account)
        self.btn_change_account.clicked.connect(self.change_account)
        self.account_phone_input.textChanged.connect(lambda text: self.phone_input.setText(self.normalize_phone(text)))
        self.account_phone_input.textChanged.connect(self._save_settings)

        self.btn_request_code.clicked.connect(self.request_code)
        self.btn_sign_in.clicked.connect(self.sign_in)
        self.btn_pick_photo.clicked.connect(self.pick_photo)
        self.btn_create_with_members.clicked.connect(lambda: self.create_groups(add_members=True))
        self.btn_create_without_members.clicked.connect(lambda: self.create_groups(add_members=False))
        self.btn_add_users_only.clicked.connect(self.add_users_without_groups)

        self.contacts_random_delay_checkbox.toggled.connect(
            lambda _: self._sync_delay_controls(self.contacts_random_delay_checkbox, self.contacts_delay_min_spin, self.contacts_delay_max_spin)
        )
        self.groups_random_delay_checkbox.toggled.connect(
            lambda _: self._sync_delay_controls(self.groups_random_delay_checkbox, self.groups_delay_min_spin, self.groups_delay_max_spin)
        )
        self.contacts_delay_min_spin.valueChanged.connect(
            lambda _: self._sync_delay_controls(self.contacts_random_delay_checkbox, self.contacts_delay_min_spin, self.contacts_delay_max_spin)
        )
        self.groups_delay_min_spin.valueChanged.connect(
            lambda _: self._sync_delay_controls(self.groups_random_delay_checkbox, self.groups_delay_min_spin, self.groups_delay_max_spin)
        )
        self.contacts_delay_max_spin.valueChanged.connect(
            lambda _: self._sync_delay_controls(self.contacts_random_delay_checkbox, self.contacts_delay_min_spin, self.contacts_delay_max_spin)
        )
        self.groups_delay_max_spin.valueChanged.connect(
            lambda _: self._sync_delay_controls(self.groups_random_delay_checkbox, self.groups_delay_min_spin, self.groups_delay_max_spin)
        )
        self.group_type_combo.currentIndexChanged.connect(lambda _: self._sync_forum_controls())

        self.auth_delay_spin.valueChanged.connect(lambda _: self._on_delay_controls_changed())
        self.contacts_delay_min_spin.valueChanged.connect(lambda _: self._on_delay_controls_changed())
        self.contacts_delay_max_spin.valueChanged.connect(lambda _: self._on_delay_controls_changed())
        self.contacts_random_delay_checkbox.toggled.connect(lambda _: self._on_delay_controls_changed())
        self.groups_delay_min_spin.valueChanged.connect(lambda _: self._on_delay_controls_changed())
        self.groups_delay_max_spin.valueChanged.connect(lambda _: self._on_delay_controls_changed())
        self.groups_random_delay_checkbox.toggled.connect(lambda _: self._on_delay_controls_changed())

        self.group_use_contacts_checkbox.toggled.connect(lambda _: self._save_settings())
        self.group_use_usernames_checkbox.toggled.connect(lambda _: self._save_settings())
        self.group_use_ids_checkbox.toggled.connect(lambda _: self._save_settings())
        self.use_members_checkbox.toggled.connect(lambda _: self._sync_group_member_inputs())
        self.group_use_contacts_checkbox.toggled.connect(lambda _: self._sync_group_member_inputs())
        self.group_use_usernames_checkbox.toggled.connect(lambda _: self._sync_group_member_inputs())
        self.group_use_ids_checkbox.toggled.connect(lambda _: self._sync_group_member_inputs())
        self.group_type_combo.currentIndexChanged.connect(lambda _: self._save_settings())
        self.topic_preset_combo.currentIndexChanged.connect(lambda _: self._save_settings())
        self.topic_custom_input.textChanged.connect(self._save_settings)
        self.group_contacts_input.textChanged.connect(self._save_settings)
        self.group_usernames_input.textChanged.connect(self._save_settings)
        self.group_user_ids_input.textChanged.connect(self._save_settings)

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

        account = data.get("account", {}) if isinstance(data, dict) else {}
        if not account and isinstance(data, dict):
            # backward compatibility with old accounts list format
            accounts_raw = data.get("accounts", [])
            if isinstance(accounts_raw, list) and accounts_raw and isinstance(accounts_raw[0], dict):
                account = accounts_raw[0]

        account_phone = self.normalize_phone(str(account.get("phone", "")))
        self.account_phone_input.setText(account_phone)
        self.phone_input.setText(account_phone)

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

        group_members = data.get("group_members", {}) if isinstance(data, dict) else {}
        self.group_use_contacts_checkbox.setChecked(
            self._to_bool(group_members.get("use_contacts"), self.group_use_contacts_checkbox.isChecked())
        )
        self.group_use_usernames_checkbox.setChecked(
            self._to_bool(group_members.get("use_usernames"), self.group_use_usernames_checkbox.isChecked())
        )
        self.group_use_ids_checkbox.setChecked(
            self._to_bool(group_members.get("use_ids"), self.group_use_ids_checkbox.isChecked())
        )
        self.group_contacts_input.setPlainText(str(group_members.get("contacts", "")))
        self.group_usernames_input.setPlainText(str(group_members.get("usernames", "")))
        self.group_user_ids_input.setPlainText(str(group_members.get("user_ids", "")))

        group_options = data.get("group_options", {}) if isinstance(data, dict) else {}
        self._set_combo_by_data(self.group_type_combo, str(group_options.get("type", "normal")))
        self._set_combo_by_data(self.topic_preset_combo, str(group_options.get("topic_preset", "")))
        self.topic_custom_input.setText(str(group_options.get("topic_custom", "")))

        self._sync_delay_controls(self.contacts_random_delay_checkbox, self.contacts_delay_min_spin, self.contacts_delay_max_spin)
        self._sync_delay_controls(self.groups_random_delay_checkbox, self.groups_delay_min_spin, self.groups_delay_max_spin)
        self._sync_forum_controls()
        self._sync_group_member_inputs()
        self._loading_settings = False

    def _save_settings(self) -> None:
        if self._loading_settings:
            return
        payload = {
            "api": {
                "api_id": self.api_id_input.text().strip(),
                "api_hash": self.api_hash_input.text().strip(),
            },
            "account": {
                "phone": self.account_phone_input.text().strip(),
            },
            "delays": {
                "auth": self.auth_delay_spin.value(),
                "contacts_min": self.contacts_delay_min_spin.value(),
                "contacts_max": self.contacts_delay_max_spin.value(),
                "contacts_random": self.contacts_random_delay_checkbox.isChecked(),
                "groups_min": self.groups_delay_min_spin.value(),
                "groups_max": self.groups_delay_max_spin.value(),
                "groups_random": self.groups_random_delay_checkbox.isChecked(),
            },
            "group_members": {
                "use_contacts": self.group_use_contacts_checkbox.isChecked(),
                "use_usernames": self.group_use_usernames_checkbox.isChecked(),
                "use_ids": self.group_use_ids_checkbox.isChecked(),
                "contacts": self.group_contacts_input.toPlainText(),
                "usernames": self.group_usernames_input.toPlainText(),
                "user_ids": self.group_user_ids_input.toPlainText(),
            },
            "group_options": {
                "type": self.group_type_combo.currentData(),
                "topic_preset": self.topic_preset_combo.currentData(),
                "topic_custom": self.topic_custom_input.text().strip(),
            },
        }
        SETTINGS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _set_combo_by_data(self, combo: QComboBox, value: str) -> None:
        idx = combo.findData(value)
        combo.setCurrentIndex(idx if idx >= 0 else 0)

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

    def save_account(self) -> None:
        phone = self.normalize_phone(self.account_phone_input.text())
        if not phone:
            self.show_error("Для аккаунта обязателен телефон")
            return

        self.account_phone_input.setText(phone)
        self.phone_input.setText(phone)
        self._save_settings()
        self.log(f"Текущий аккаунт сохранён: {phone}")

    def change_account(self) -> None:
        self.phone_code_hash = None
        self.account_phone_input.clear()
        self.phone_input.clear()
        self.code_input.clear()
        self.password_input.clear()
        self._save_settings()
        self.log("Режим смены аккаунта: введите новый телефон и снова выполните авторизацию")

    def _client_from_fields(self) -> TelegramClient:
        api_id = self.api_id_input.text().strip()
        api_hash = self.api_hash_input.text().strip()
        if not api_id.isdigit() or not api_hash:
            raise ValueError("Сначала заполните API ID + API HASH")

        phone = self.normalize_phone(self.account_phone_input.text() or self.phone_input.text())
        if not phone:
            raise ValueError("Укажите телефон текущего аккаунта")

        session = self.session_from_phone(phone)
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

    @staticmethod
    async def retry_async(
        operation: Callable[[], Awaitable[object]],
        *,
        attempts: int = RETRY_ATTEMPTS,
        base_delay: float = RETRY_BASE_DELAY,
        retry_exceptions: tuple[type[BaseException], ...] = (Exception,),
        on_retry: Optional[Callable[[int, BaseException], None]] = None,
    ) -> object:
        if attempts < 1:
            raise ValueError("attempts должен быть >= 1")

        last_exc: Optional[BaseException] = None
        for attempt in range(1, attempts + 1):
            try:
                return await operation()
            except retry_exceptions as exc:  # noqa: PERF203
                last_exc = exc
                if attempt >= attempts:
                    break
                if on_retry:
                    on_retry(attempt, exc)
                delay = base_delay * attempt
                if delay > 0:
                    await asyncio.sleep(delay)

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("retry_async завершился без результата")

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
    def _sync_delay_controls(random_checkbox: QCheckBox, min_spin: QSpinBox, max_spin: QSpinBox) -> None:
        is_random = random_checkbox.isChecked()
        max_spin.setEnabled(is_random)

        if not is_random:
            max_spin.setValue(min_spin.value())
            return

        if max_spin.value() < min_spin.value():
            max_spin.setValue(min_spin.value())

    def _sync_group_member_inputs(self) -> None:
        use_members = self.use_members_checkbox.isChecked()
        contacts_enabled = use_members and self.group_use_contacts_checkbox.isChecked()
        refs_enabled = use_members and self.group_use_usernames_checkbox.isChecked()
        ids_enabled = use_members and self.group_use_ids_checkbox.isChecked()

        self.group_contacts_input.setEnabled(contacts_enabled)
        self.group_usernames_input.setEnabled(refs_enabled)
        self.group_user_ids_input.setEnabled(ids_enabled)

    def _sync_forum_controls(self) -> None:
        is_forum = self.group_type_combo.currentData() == "forum"
        self.topic_preset_combo.setEnabled(is_forum)
        self.topic_custom_input.setEnabled(is_forum)

    def run_task(
        self,
        fn: Callable[[], object],
        success_cb: Optional[Callable[[object], None]] = None,
        *,
        block_ui: bool = True,
    ) -> None:
        _ = block_ui
        self._task_queue.append((fn, success_cb))
        if self._task_in_progress:
            queued = len(self._task_queue)
            self.statusBar().showMessage(f"Операция добавлена в очередь. В очереди: {queued}")
            self.log(f"[QUEUE] Операция добавлена в очередь. В очереди: {queued}")
            return

        self._process_next_task()

    def _process_next_task(self) -> None:
        if not self._task_queue:
            self._task_in_progress = False
            self.statusBar().showMessage("Готово")
            return

        self._task_in_progress = True
        self.statusBar().showMessage("Выполняется операция... Остальные действия будут обработаны по очереди")

        fn, success_cb = self._task_queue.popleft()
        try:
            result = fn()
            if success_cb:
                success_cb(result)
        except Exception as exc:  # noqa: BLE001
            self.show_error(str(exc), exc)
        finally:
            self._process_next_task()

    def set_busy(self, busy: bool) -> None:
        _ = busy

    def log(self, text: str) -> None:
        self.log_output.appendPlainText(text)

    def show_error(self, text: str, exc: Exception | None = None) -> None:
        self.log(f"[ОШИБКА] {text}")
        print(f"[ОШИБКА] {text}", file=sys.stderr)
        if exc is not None:
            traceback.print_exception(type(exc), exc, exc.__traceback__)

    @staticmethod
    def normalize_phone(phone: str) -> str:
        cleaned = phone.strip()
        if not cleaned:
            return ""

        if cleaned.startswith("+"):
            digits = re.sub(r"\D", "", cleaned)
            return f"+{digits}" if digits else ""

        digits = re.sub(r"\D", "", cleaned)
        return f"+{digits}" if digits else ""

    @staticmethod
    def session_from_phone(phone: str) -> str:
        digits = re.sub(r"\D", "", phone)
        if not digits:
            return "tg_session_default"
        return f"tg_session_{digits}"

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

            full_name = re.sub(r"\s+", " ", person_line).strip()
            tokens = [t for t in full_name.split() if t]
            if len(tokens) < 2:
                raise ValueError(f"Не удалось распознать ФИО: {person_line}")

            m = re.search(r"(?:\+)?\d{10,15}", link_line)
            if not m:
                raise ValueError(f"Не удалось извлечь телефон: {link_line}")

            phone = MainWindow.normalize_phone(m.group(0))
            if not phone:
                raise ValueError(f"Не удалось извлечь телефон: {link_line}")

            contacts.append(ContactData(first_name=full_name, last_name="", phone=phone))
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

    async def _get_entity_with_retry(self, client: TelegramClient, value: str | int):
        async def _op():
            return await client.get_entity(value)

        return await self.retry_async(
            _op,
            attempts=RETRY_ATTEMPTS,
            base_delay=RETRY_BASE_DELAY,
            on_retry=lambda attempt, exc: self.log(f"retry get_entity({value}) attempt={attempt + 1}: {exc}"),
        )


    def _run_async_task(self, coro: Awaitable[object]) -> object:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def pick_photo(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "Выберите фото", "", "Images (*.jpg *.jpeg *.png *.webp)")
        if p:
            self.photo_path_input.setText(p)

    def request_code(self) -> None:
        phone = self.normalize_phone(self.phone_input.text())
        if not phone:
            self.show_error("Введите телефон")
            return
        self.phone_input.setText(phone)
        self.account_phone_input.setText(phone)

        def job() -> str:
            async def _inner() -> str:
                if self._auth_client is not None:
                    await self._auth_client.disconnect()
                    self._auth_client = None
                    self._auth_phone = ""

                client = self._client_from_fields()
                try:
                    await client.connect()
                    await self.wait_auth_delay()
                    sent = await client.send_code_request(phone)
                    self._auth_client = client
                    self._auth_phone = phone
                    return sent.phone_code_hash
                except Exception:
                    await client.disconnect()
                    raise

            return self._run_async_task(_inner())

        self.log(f"[AUTH] Отправка кода на {phone}...")

        def _on_code_sent(phone_code_hash: object) -> None:
            self.phone_code_hash = str(phone_code_hash)
            self.log("[AUTH] Код отправлен. Введите код из Telegram и нажмите «Войти в аккаунт».")
            self.statusBar().showMessage("Код отправлен. Введите код из Telegram")
            self.code_input.setFocus()

        self.run_task(job, success_cb=_on_code_sent)

    def sign_in(self) -> None:
        phone = self.normalize_phone(self.phone_input.text())
        code = self.code_input.text().strip()
        pwd = self.password_input.text().strip()
        if not self.phone_code_hash:
            self.show_error("Сначала отправьте код")
            return

        def job() -> str:
            async def _inner() -> str:
                self.log(f"[AUTH] Начало входа для {phone}")
                reusing_auth_client = self._auth_client is not None and self._auth_phone == phone
                client = self._auth_client if reusing_auth_client else self._client_from_fields()

                if not reusing_auth_client:
                    self.log("[AUTH] Подключение к Telegram...")
                    await client.connect()
                    self.log("[AUTH] Подключение успешно")

                await self.wait_auth_delay()
                try:
                    self.log("[AUTH] Выполняется sign_in по коду...")
                    await client.sign_in(phone=phone, code=code, phone_code_hash=self.phone_code_hash)
                    self.log("[AUTH] Вход по коду выполнен")
                    result = "Успешный вход в аккаунт"
                except SessionPasswordNeededError:
                    self.log("[AUTH] Требуется 2FA пароль")
                    if not pwd:
                        raise ValueError("Нужен 2FA пароль")
                    await client.sign_in(password=pwd)
                    self.log("[AUTH] Вход по 2FA выполнен")
                    result = "Успешный вход по 2FA"
                except Exception:
                    if not reusing_auth_client:
                        await client.disconnect()
                    raise

                self.log("[AUTH] Отключение клиента")
                await client.disconnect()
                if reusing_auth_client:
                    self._auth_client = None
                    self._auth_phone = ""
                return result

            return self._run_async_task(_inner())

        self.log("[AUTH] Выполняется вход...")
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
            self.show_error(str(exc), exc)
            return

        def job() -> str:
            async def _inner() -> str:
                client = self._client_from_fields()
                try:
                    await client.connect()
                    if not await client.is_user_authorized():
                        raise ValueError("Сначала выполните вход")

                    logs = [
                        f"Старт обработки пользователей: contacts={len(contacts)}, refs={len(refs)}, ids={len(user_ids)}",
                        "Проверка авторизации: успешно",
                    ]
                    if contacts:
                        logs.append("Импорт контактов: старт")
                        await self.wait_contacts_delay()
                        batch = [InputPhoneContact(client_id=i + 1, phone=c.phone, first_name=c.first_name, last_name=c.last_name) for i, c in enumerate(contacts)]
                        imported = await client(ImportContactsRequest(batch))
                        logs.append(f"Импорт контактов: отправлено {len(batch)}")
                        logs.append(f"Импортировано контактов: {len(contacts)}")
                        logs.append(f"Telegram-профилей найдено: {len(imported.users)}")

                    if refs:
                        logs.append("Проверка username/ссылок: старт")
                        ok, fail = 0, 0
                        for ref in refs:
                            await self.wait_contacts_delay()
                            try:
                                await self._get_entity_with_retry(client, ref)
                                ok += 1
                                logs.append(f"username/link OK: {ref}")
                            except Exception as exc:
                                fail += 1
                                logs.append(f"username/link FAIL: {ref} ({exc})")
                        logs.append(f"Проверка username/ссылок: ok={ok}, fail={fail}")

                    if user_ids:
                        logs.append("Проверка user ID: старт")
                        ok_ids, fail_ids = 0, 0
                        for uid in user_ids:
                            await self.wait_contacts_delay()
                            try:
                                await self._get_entity_with_retry(client, uid)
                                ok_ids += 1
                                logs.append(f"user ID OK: {uid}")
                            except Exception as exc:
                                fail_ids += 1
                                logs.append(f"user ID FAIL: {uid} ({exc})")
                        logs.append(f"Проверка user ID: ok={ok_ids}, fail={fail_ids}")

                    return "\n".join(logs) if logs else "Нет данных для обработки"
                finally:
                    await client.disconnect()

            return self._run_async_task(_inner())

        self.log("Запущена обработка пользователей...")
        self.run_task(job, success_cb=lambda result: self.log(str(result)))

    def create_groups(self, add_members: bool) -> None:
        title = self.group_title_input.text().strip()
        about = self.group_about_input.text().strip()
        cnt = self.group_count_spin.value()
        photo = self.photo_path_input.text().strip()
        is_forum = self.group_type_combo.currentData() == "forum"
        topic_title = self.topic_custom_input.text().strip() or str(self.topic_preset_combo.currentData() or "").strip()

        if not title:
            self.show_error("Введите название группы")
            return

        add_members = add_members and self.use_members_checkbox.isChecked()
        try:
            contacts = (
                self.parse_contacts(self.group_contacts_input.toPlainText())
                if add_members and self.group_use_contacts_checkbox.isChecked() and self.group_contacts_input.toPlainText().strip()
                else []
            )
            refs = (
                self.parse_user_refs(self.group_usernames_input.toPlainText())
                if add_members and self.group_use_usernames_checkbox.isChecked() and self.group_usernames_input.toPlainText().strip()
                else []
            )
            user_ids = (
                self.parse_user_ids(self.group_user_ids_input.toPlainText())
                if add_members and self.group_use_ids_checkbox.isChecked() and self.group_user_ids_input.toPlainText().strip()
                else []
            )

            selected_sources = [
                self.group_use_contacts_checkbox.isChecked(),
                self.group_use_usernames_checkbox.isChecked(),
                self.group_use_ids_checkbox.isChecked(),
            ]
            if add_members and not any(selected_sources):
                self.show_error("Выберите хотя бы один источник участников (Контакты, Юзернеймы/ссылки или ID)")
                return

            if add_members and not (contacts or refs or user_ids):
                self.show_error("Для добавления участников заполните хотя бы одно выбранное поле в разделе «Группы»")
                return
        except Exception as exc:  # noqa: BLE001
            self.show_error(str(exc), exc)
            return

        def job() -> str:
            async def _inner() -> str:
                client = self._client_from_fields()
                try:
                    await client.connect()
                    if not await client.is_user_authorized():
                        raise ValueError("Сначала выполните вход")

                    users_to_invite = []
                    logs: list[str] = [f"Старт создания групп: count={cnt}, add_members={add_members}, forum={is_forum}"]
                    if add_members:
                        if contacts:
                            logs.append(f"Подготовка участников из контактов: {len(contacts)}")
                            await self.wait_contacts_delay()
                            batch = [InputPhoneContact(client_id=i + 1, phone=c.phone, first_name=c.first_name, last_name=c.last_name) for i, c in enumerate(contacts)]
                            imported = await client(ImportContactsRequest(batch))
                            from_contacts = [u for u in imported.users if not getattr(u, "bot", False)]
                            users_to_invite.extend(from_contacts)
                            logs.append(f"Получено пользователей из контактов: {len(from_contacts)}")

                        for ref in refs:
                            await self.wait_contacts_delay()
                            try:
                                ent = await self._get_entity_with_retry(client, ref)
                                if not getattr(ent, "bot", False):
                                    users_to_invite.append(ent)
                                    logs.append(f"Добавлен кандидат по username/link: {ref}")
                            except Exception as exc:
                                logs.append(f"Не удалось получить username/link {ref}: {exc}")

                        for uid in user_ids:
                            await self.wait_contacts_delay()
                            try:
                                ent = await self._get_entity_with_retry(client, uid)
                                if not getattr(ent, "bot", False):
                                    users_to_invite.append(ent)
                                    logs.append(f"Добавлен кандидат по user ID: {uid}")
                            except Exception as exc:
                                logs.append(f"Не удалось получить user ID {uid}: {exc}")

                        uniq = {getattr(u, "id", None): u for u in users_to_invite if getattr(u, "id", None) is not None}
                        users_to_invite = list(uniq.values())
                        logs.append(f"Подготовлено участников после дедупликации: {len(users_to_invite)}")

                    for i in range(cnt):
                        await self.wait_groups_delay()
                        gname = title
                        res = await client(CreateChannelRequest(title=gname, about=about, megagroup=True, forum=is_forum))
                        channel = res.chats[0]
                        logs.append(f"Создана группа: {gname}")

                        if is_forum and topic_title:
                            await self.wait_groups_delay()
                            messages_mod = import_module("telethon.tl.functions.messages")
                            create_topic_request = getattr(messages_mod, "CreateForumTopicRequest")
                            await client(create_topic_request(peer=channel, title=topic_title))
                            logs.append(f"Создана тема в {gname}: {topic_title}")

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

                    return "\n".join(logs)
                finally:
                    await client.disconnect()

            return self._run_async_task(_inner())

        self.log("Запущено создание групп...")
        self.run_task(job, success_cb=lambda result: self.log(str(result)))


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
