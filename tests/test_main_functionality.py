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
