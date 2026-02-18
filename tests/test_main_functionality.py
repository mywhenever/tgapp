import asyncio
import sys
import types
from pathlib import Path

# Allow importing repository root module
sys.path.append(str(Path(__file__).resolve().parents[1]))


# ---- Lightweight stubs for optional heavy deps (PySide6, telethon) ----
if "PySide6" not in sys.modules:
    pyside6 = types.ModuleType("PySide6")
    qtcore = types.ModuleType("PySide6.QtCore")
    qtwidgets = types.ModuleType("PySide6.QtWidgets")

    class _Dummy:
        def __init__(self, *args, **kwargs):
            pass

    class _QLineEdit(_Dummy):
        class EchoMode:
            Password = 1

    for name in ["QObject", "QRunnable", "QThreadPool", "Signal"]:
        setattr(qtcore, name, _Dummy)

    for name, cls in {
        "QApplication": _Dummy,
        "QCheckBox": _Dummy,
        "QComboBox": _Dummy,
        "QFileDialog": _Dummy,
        "QFormLayout": _Dummy,
        "QGroupBox": _Dummy,
        "QHBoxLayout": _Dummy,
        "QLabel": _Dummy,
        "QLineEdit": _QLineEdit,
        "QMainWindow": _Dummy,
        "QMessageBox": _Dummy,
        "QPushButton": _Dummy,
        "QPlainTextEdit": _Dummy,
        "QSpinBox": _Dummy,
        "QTabWidget": _Dummy,
        "QVBoxLayout": _Dummy,
        "QWidget": _Dummy,
    }.items():
        setattr(qtwidgets, name, cls)

    sys.modules["PySide6"] = pyside6
    sys.modules["PySide6.QtCore"] = qtcore
    sys.modules["PySide6.QtWidgets"] = qtwidgets

if "telethon" not in sys.modules:
    telethon = types.ModuleType("telethon")
    telethon.TelegramClient = object

    errors = types.ModuleType("telethon.errors")
    errors.SessionPasswordNeededError = Exception
    errors.FloodWaitError = Exception

    ch_funcs = types.ModuleType("telethon.tl.functions.channels")
    ch_funcs.CreateChannelRequest = object
    ch_funcs.EditPhotoRequest = object
    ch_funcs.InviteToChannelRequest = object

    c_funcs = types.ModuleType("telethon.tl.functions.contacts")
    c_funcs.ImportContactsRequest = object

    types_mod = types.ModuleType("telethon.tl.types")
    types_mod.InputChatUploadedPhoto = object
    types_mod.InputPhoneContact = object

    sys.modules["telethon"] = telethon
    sys.modules["telethon.errors"] = errors
    sys.modules["telethon.tl.functions.channels"] = ch_funcs
    sys.modules["telethon.tl.functions.contacts"] = c_funcs
    sys.modules["telethon.tl.types"] = types_mod


import pytest

from main import MainWindow


def test_parse_user_refs_normalizes_links_and_handles_at_prefix() -> None:
    raw = "@durov\ntelegram\nhttps://t.me/example_username\nhttp://t.me/abc/"
    assert MainWindow.parse_user_refs(raw) == ["durov", "telegram", "example_username", "abc"]


def test_parse_user_refs_rejects_invite_link() -> None:
    with pytest.raises(ValueError, match="нельзя добавить напрямую"):
        MainWindow.parse_user_refs("https://t.me/+79990001122")


def test_parse_user_ids_valid_and_invalid() -> None:
    assert MainWindow.parse_user_ids("123\n456") == [123, 456]
    with pytest.raises(ValueError, match="Некорректный ID"):
        MainWindow.parse_user_ids("12a")


def test_parse_contacts_success() -> None:
    raw = "Иванов Иван Иванович 01.01.1990\nhttps://t.me/+79990001122"
    contacts = MainWindow.parse_contacts(raw)
    assert len(contacts) == 1
    assert contacts[0].first_name == "Иванов Иван Иванович 01.01.1990"
    assert contacts[0].last_name == ""
    assert contacts[0].phone == "+79990001122"


def test_normalize_phone_adds_plus_when_missing() -> None:
    assert MainWindow.normalize_phone("79990001122") == "+79990001122"
    assert MainWindow.normalize_phone("+79990001122") == "+79990001122"


def test_parse_contacts_auto_adds_plus_when_missing() -> None:
    raw = "Иванов Иван Иванович 01.01.1990\nhttps://t.me/79990001122"
    contacts = MainWindow.parse_contacts(raw)
    assert contacts[0].phone == "+79990001122"


def test_retry_async_succeeds_after_retries() -> None:
    state = {"calls": 0}

    async def op() -> str:
        state["calls"] += 1
        if state["calls"] < 3:
            raise RuntimeError("temporary")
        return "ok"

    retried = []

    result = asyncio.run(
        MainWindow.retry_async(
            op,
            attempts=3,
            base_delay=0,
            retry_exceptions=(RuntimeError,),
            on_retry=lambda attempt, exc: retried.append((attempt, str(exc))),
        )
    )

    assert result == "ok"
    assert state["calls"] == 3
    assert retried == [(1, "temporary"), (2, "temporary")]


def test_retry_async_raises_after_max_attempts() -> None:
    async def op() -> None:
        raise RuntimeError("always-fail")

    with pytest.raises(RuntimeError, match="always-fail"):
        asyncio.run(MainWindow.retry_async(op, attempts=2, base_delay=0, retry_exceptions=(RuntimeError,)))


def test_retry_async_does_not_catch_unlisted_exception() -> None:
    async def op() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        asyncio.run(MainWindow.retry_async(op, attempts=3, base_delay=0, retry_exceptions=(RuntimeError,)))


def test_retry_async_validates_attempts() -> None:
    async def op() -> str:
        return "ok"

    with pytest.raises(ValueError, match="attempts"):
        asyncio.run(MainWindow.retry_async(op, attempts=0))


def test_session_from_phone_uses_digits_only() -> None:
    assert MainWindow.session_from_phone("+7 (999) 000-11-22") == "tg_session_79990001122"


def test_sync_group_member_inputs_keeps_id_field_editable_when_members_enabled() -> None:
    class _Toggle:
        def __init__(self, checked: bool):
            self._checked = checked

        def isChecked(self) -> bool:
            return self._checked

    class _Input:
        def __init__(self):
            self.enabled = None

        def setEnabled(self, enabled: bool) -> None:
            self.enabled = enabled

    fake_window = types.SimpleNamespace(
        use_members_checkbox=_Toggle(True),
        group_use_contacts_checkbox=_Toggle(False),
        group_use_usernames_checkbox=_Toggle(False),
        group_use_ids_checkbox=_Toggle(False),
        group_contacts_input=_Input(),
        group_usernames_input=_Input(),
        group_user_ids_input=_Input(),
    )

    MainWindow._sync_group_member_inputs(fake_window)

    assert fake_window.group_contacts_input.enabled is True
    assert fake_window.group_usernames_input.enabled is True
    assert fake_window.group_user_ids_input.enabled is True


def test_build_proxy_config_returns_none_when_disabled() -> None:
    class _Toggle:
        def __init__(self, checked: bool):
            self._checked = checked

        def isChecked(self) -> bool:
            return self._checked

    fake_window = types.SimpleNamespace(proxy_enabled_checkbox=_Toggle(False))

    assert MainWindow._build_proxy_config(fake_window) is None


def test_build_proxy_config_builds_tuple_for_socks5(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Toggle:
        def __init__(self, checked: bool):
            self._checked = checked

        def isChecked(self) -> bool:
            return self._checked

    class _Line:
        def __init__(self, value: str):
            self._value = value

        def text(self) -> str:
            return self._value

    class _Combo:
        def __init__(self, value: str):
            self._value = value

        def currentData(self) -> str:
            return self._value

    class _Port:
        def __init__(self, value: int):
            self._value = value

        def value(self) -> int:
            return self._value

    socks_stub = types.SimpleNamespace(SOCKS5=123, HTTP=456)
    monkeypatch.setattr('main.import_module', lambda name: socks_stub if name == 'socks' else None)

    fake_window = types.SimpleNamespace(
        proxy_enabled_checkbox=_Toggle(True),
        proxy_host_input=_Line('127.0.0.1'),
        proxy_type_combo=_Combo('socks5'),
        proxy_port_spin=_Port(1080),
        proxy_username_input=_Line('user'),
        proxy_password_input=_Line('pass'),
    )

    proxy = MainWindow._build_proxy_config(fake_window)
    assert proxy == (123, '127.0.0.1', 1080, True, 'user', 'pass')
