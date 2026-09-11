from __future__ import annotations

import html
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from .config import Settings
from .storage import JsonStore


log = logging.getLogger("playerok_minimal.core")
DEFAULT_PLAYEROK_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)


class Core:
    def __init__(self, bot: Any, settings: Settings):
        self.bot = bot
        self.settings = settings
        self.data_dir = settings.data_dir
        self.plugins = None
        self._states: dict[int, tuple[str, dict[str, Any]]] = {}
        self._state_lock = threading.RLock()
        self._account = None
        self._account_lock = threading.RLock()
        self._playerok_store = JsonStore(
            self.data_dir / "playerok_connection.json",
            {
                "cookies": "",
                "user_agent": "",
                "username": "",
                "updated_at": "",
            },
        )
        self._playerok = self._playerok_store.load()

    @property
    def admin_id(self) -> int:
        return self.settings.admin_id

    def is_admin(self, value: Any) -> bool:
        try:
            return int(value) == self.admin_id
        except (TypeError, ValueError):
            return False

    def send(self, chat_id: int, text: str, reply_markup: Any = None) -> Any:
        return self.bot.send_message(
            chat_id,
            text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )

    def edit(self, call: Any, text: str, reply_markup: Any = None) -> Any:
        try:
            return self.bot.edit_message_text(
                text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=reply_markup,
            )
        except Exception:
            return self.send(call.message.chat.id, text, reply_markup)

    def answer(self, call: Any, text: str = "") -> None:
        try:
            self.bot.answer_callback_query(call.id, text=text[:180])
        except Exception:
            pass

    def set_state(self, chat_id: int, name: str, payload: dict[str, Any] | None = None) -> None:
        with self._state_lock:
            self._states[int(chat_id)] = (name, dict(payload or {}))

    def get_state(self, chat_id: int) -> tuple[str, dict[str, Any]]:
        with self._state_lock:
            return self._states.get(int(chat_id), ("", {}))

    def clear_state(self, chat_id: int) -> None:
        with self._state_lock:
            self._states.pop(int(chat_id), None)

    def plugin_data_dir(self, plugin_uuid: str) -> Path:
        safe = "".join(char for char in str(plugin_uuid) if char.isalnum() or char in "-_")
        path = self.data_dir / "plugin_data" / (safe or "unknown")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def playerok_is_configured(self) -> bool:
        cookies = str(self._playerok.get("cookies") or "")
        user_agent = str(self._playerok.get("user_agent") or "")
        return "token=" in cookies.replace(" ", "") and len(user_agent) >= 10

    def playerok_connection_info(self) -> dict[str, Any]:
        cookies = str(self._playerok.get("cookies") or "")
        return {
            "configured": self.playerok_is_configured(),
            "username": str(self._playerok.get("username") or ""),
            "token_tail": self._token_tail(cookies),
            "updated_at": str(self._playerok.get("updated_at") or ""),
        }

    @staticmethod
    def _token_tail(cookies: str) -> str:
        for part in str(cookies or "").split(";"):
            name, separator, value = part.strip().partition("=")
            if separator and name.strip() == "token":
                return value.strip()[-4:]
        return ""

    @staticmethod
    def _new_playerok_account(cookies: str, user_agent: str) -> Any:
        from playerokapi.account import Account

        if "instance" in getattr(Account, "__dict__", {}):
            delattr(Account, "instance")
        return Account(
            cookies=cookies,
            user_agent=user_agent,
            requests_timeout=25,
        ).get()

    def configure_playerok(self, cookies: str, user_agent: str = "") -> Any:
        cookies = str(cookies or "").strip()
        user_agent = (
            str(user_agent or "").strip()
            or str(self._playerok.get("user_agent") or "").strip()
            or DEFAULT_PLAYEROK_USER_AGENT
        )
        if "token=" not in cookies.replace(" ", ""):
            raise ValueError("в Cookie отсутствует token=...")
        with self._account_lock:
            account = self._new_playerok_account(cookies, user_agent)
            self._playerok = {
                "cookies": cookies,
                "user_agent": user_agent,
                "username": str(getattr(account, "username", "") or ""),
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            self._playerok_store.save(self._playerok)
            self._account = account
            return account

    def clear_playerok_connection(self) -> None:
        with self._account_lock:
            self._playerok = {
                "cookies": "",
                "user_agent": "",
                "username": "",
                "updated_at": "",
            }
            self._playerok_store.save(self._playerok)
            self._account = None

    def playerok_account(self, refresh: bool = False) -> Any:
        with self._account_lock:
            if self._account is not None and not refresh:
                return self._account
            if not self.playerok_is_configured():
                raise RuntimeError("Playerok не подключён. Откройте плагин и выполните настройку в Telegram")
            self._account = self._new_playerok_account(
                str(self._playerok.get("cookies") or ""),
                str(self._playerok.get("user_agent") or ""),
            )
            return self._account

    def request_restart(self, delay: float = 1.5) -> None:
        def terminate() -> None:
            time.sleep(max(0.2, delay))
            os._exit(0)

        threading.Thread(target=terminate, daemon=True, name="restart-bot").start()

    @staticmethod
    def esc(value: Any) -> str:
        return html.escape(str(value if value is not None else ""), quote=False)
