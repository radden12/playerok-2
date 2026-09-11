from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import telebot
from telebot import types

from .config import Settings
from .core import Core
from .plugin_manager import PluginInfo, PluginManager


log = logging.getLogger("playerok_minimal.app")


class App:
    def __init__(self):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        self.settings = Settings.from_env()
        self.bot = telebot.TeleBot(self.settings.bot_token, parse_mode="HTML", threaded=True)
        self.core = Core(self.bot, self.settings)
        bundled = Path(__file__).resolve().parent.parent / "bundled_plugins"
        self.plugins = PluginManager(self.core, bundled)
        self.core.plugins = self.plugins
        self.plugins.seed_bundled_once()
        self._register_core_handlers()
        self.plugins.load_all()

    def _home_keyboard(self) -> types.InlineKeyboardMarkup:
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        for index, info in enumerate(self._plugin_rows()):
            if info.loaded and info.enabled:
                keyboard.add(types.InlineKeyboardButton(
                    "🔑 " + info.name,
                    callback_data="home:plugin:" + str(index),
                ))
        keyboard.add(types.InlineKeyboardButton("🧩 Управление плагинами", callback_data="home:plugins"))
        return keyboard

    def _home(self, chat_id: int) -> None:
        self.core.clear_state(chat_id)
        self.core.send(
            chat_id,
            "✅ <b>Вход выполнен.</b>\n\n"
            "🤖 <b>Playerok Control</b>\n"
            "<i>Личная панель управления магазином</i>\n\n"
            "Выберите нужный раздел. Список обновляется автоматически после установки, "
            "обновления или удаления плагина.",
            self._home_keyboard(),
        )

    def _plugin_rows(self) -> list[PluginInfo]:
        return list(self.plugins.infos.values())

    def _info_by_index(self, raw: str) -> PluginInfo | None:
        try:
            index = int(raw)
        except (TypeError, ValueError):
            return None
        rows = self._plugin_rows()
        return rows[index] if 0 <= index < len(rows) else None

    def _plugins_screen(self, target: Any) -> None:
        rows = self._plugin_rows()
        lines = [
            "🧩 <b>Управление плагинами</b>", "",
            "Здесь можно открыть, включить, выключить, обновить или удалить плагин.", "",
            "Установлено: <b>" + str(len(rows)) + "</b>",
        ]
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        for index, info in enumerate(rows):
            mark = "🟢" if info.loaded else ("⏸" if not info.enabled else "🔴")
            state = "работает" if info.loaded else ("выключен" if not info.enabled else "ошибка")
            lines.append(mark + " <b>" + self.core.esc(info.name) + "</b> — " + state)
            if info.error:
                lines.append("<code>" + self.core.esc(info.error[:300]) + "</code>")
            keyboard.add(types.InlineKeyboardButton(
                mark + " " + info.name,
                callback_data="pm:card:" + str(index),
            ))
        if not rows:
            lines.append("Плагинов пока нет.")
        lines += [
            "",
            "Файлы, загруженные через Telegram, сохраняются в <code>/app/data/plugins</code> "
            "и не пропадают после пересборки Bothost.",
        ]
        keyboard.add(types.InlineKeyboardButton("➕ Добавить или обновить плагин", callback_data="pm:add"))
        keyboard.add(types.InlineKeyboardButton("🏠 Главное меню", callback_data="home:start"))
        text = "\n".join(lines)
        if hasattr(target, "message"):
            self.core.edit(target, text, keyboard)
        else:
            self.core.send(int(target), text, keyboard)

    def _open_plugin(self, info: PluginInfo, chat_id: int) -> None:
        if info.uuid == "kosell-playerok-clean-v1" and not self.core.playerok_is_configured():
            self._begin_playerok_setup(chat_id, return_to_primary=True)
            return
        try:
            self.plugins.open(info, chat_id)
        except Exception as exc:
            self.core.send(chat_id, "❌ " + self.core.esc(exc))

    def _open_primary(self, chat_id: int) -> None:
        info = self.plugins.primary()
        if info is None:
            self.core.send(chat_id, "Работающий плагин не найден.")
            self._plugins_screen(chat_id)
            return
        self._open_plugin(info, chat_id)

    def _plugin_card(self, target: Any, index: int) -> None:
        info = self._info_by_index(str(index))
        if info is None:
            self._plugins_screen(target)
            return
        status = "🟢 работает" if info.loaded else ("⏸ выключен" if not info.enabled else "🔴 ошибка")
        text = (
            "⚙️ <b>" + self.core.esc(info.name) + "</b>\n\n"
            "Статус: <b>" + status + "</b>\n"
            "Версия: <code>" + self.core.esc(info.version) + "</code>\n"
            "Файл: <code>" + self.core.esc(info.filename) + "</code>\n"
            "Источник: <code>" + self.core.esc(str(info.path)) + "</code>"
        )
        if info.author:
            text += "\nАвтор: <b>" + self.core.esc(info.author) + "</b>"
        if info.description:
            text += "\n\n" + self.core.esc(info.description)
        if info.error:
            text += "\n\nОшибка:\n<code>" + self.core.esc(info.error[:1000]) + "</code>"
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        if info.loaded:
            keyboard.add(types.InlineKeyboardButton("⚙️ Открыть настройки", callback_data="pm:open:" + str(index)))
        keyboard.add(types.InlineKeyboardButton(
            "⏸ Выключить" if info.enabled else "▶️ Включить",
            callback_data="pm:toggle:" + str(index),
        ))
        keyboard.add(types.InlineKeyboardButton("🔄 Обновить этот плагин", callback_data="pm:update:" + str(index)))
        keyboard.add(types.InlineKeyboardButton("🗑 Удалить плагин", callback_data="pm:ask:" + str(index)))
        keyboard.add(types.InlineKeyboardButton("⬅️ Управление плагинами", callback_data="home:plugins"))
        if hasattr(target, "message"):
            self.core.edit(target, text, keyboard)
        else:
            self.core.send(int(target), text, keyboard)

    def _begin_playerok_setup(self, chat_id: int, return_to_primary: bool = False) -> None:
        self.core.set_state(
            chat_id,
            "playerok_cookies",
            {"return_to_primary": bool(return_to_primary)},
        )
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton("❌ Отмена", callback_data="home:start"))
        self.core.send(
            chat_id,
            "🔗 <b>Подключение Playerok</b>\n\n"
            "Пришлите одной строкой Cookie из браузера Playerok. В строке обязательно должно быть "
            "<code>token=...</code>. При наличии добавьте <code>__ddg5_=...</code>.\n\n"
            "Больше ничего вводить не нужно. Сообщение с Cookie бот удалит сразу после чтения.",
            keyboard,
        )

    def _playerok_screen(self, target: Any) -> None:
        info = self.core.playerok_connection_info()
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        keyboard.add(types.InlineKeyboardButton(
            "🔄 Подключить заново" if info["configured"] else "🔗 Подключить Playerok",
            callback_data="cfg:playerok:start",
        ))
        if info["configured"]:
            keyboard.add(types.InlineKeyboardButton("🗑 Удалить подключение", callback_data="cfg:playerok:ask_clear"))
        keyboard.add(types.InlineKeyboardButton("⬅️ В главное меню", callback_data="home:start"))
        status = "подключён" if info["configured"] else "не подключён"
        text = "🔗 <b>Playerok</b>\n\nСтатус: <b>" + status + "</b>"
        if info["username"]:
            text += "\nАккаунт: <b>" + self.core.esc(info["username"]) + "</b>"
        if info["token_tail"]:
            text += "\nToken: <code>••••" + self.core.esc(info["token_tail"]) + "</code>"
        text += "\n\nCookie хранится только в <code>/app/data/playerok_connection.json</code>."
        if hasattr(target, "message"):
            self.core.edit(target, text, keyboard)
        else:
            self.core.send(int(target), text, keyboard)

    def _delete_sensitive_message(self, message: Any) -> None:
        try:
            self.bot.delete_message(message.chat.id, message.message_id)
        except Exception:
            pass

    def _handle_playerok_input(self, message: Any) -> None:
        state, payload = self.core.get_state(message.chat.id)
        if state != "playerok_cookies":
            return
        value = str(message.text or "").strip()
        self._delete_sensitive_message(message)
        if "token=" not in value.replace(" ", ""):
            self.core.send(message.chat.id, "❌ В строке нет <code>token=...</code>. Пришлите Cookie ещё раз.")
            return
        return_to_primary = bool(payload.get("return_to_primary"))
        try:
            account = self.core.configure_playerok(value)
        except Exception as exc:
            self.core.clear_state(message.chat.id)
            keyboard = types.InlineKeyboardMarkup(row_width=1)
            keyboard.add(types.InlineKeyboardButton("🔄 Повторить подключение", callback_data="cfg:playerok:start"))
            keyboard.add(types.InlineKeyboardButton("⬅️ В главное меню", callback_data="home:start"))
            self.core.send(
                message.chat.id,
                "❌ <b>Playerok не подключён</b>\n<code>" + self.core.esc(exc) + "</code>\n\n"
                "Данные не сохранены.",
                keyboard,
            )
            return
        self.core.clear_state(message.chat.id)
        username = str(getattr(account, "username", "") or "аккаунт подтверждён")
        self.core.send(message.chat.id, "✅ Playerok подключён: <b>" + self.core.esc(username) + "</b>")
        if return_to_primary:
            self._open_primary(message.chat.id)
        else:
            self._playerok_screen(message.chat.id)

    def _register_core_handlers(self) -> None:
        @self.bot.message_handler(commands=["start"])
        def start(message: Any) -> None:
            sender_id = int(getattr(message.from_user, "id", 0) or 0)
            log.info("Получен /start: telegram_id=%s, ADMIN_ID=%s", sender_id, self.core.admin_id)
            if self.core.is_admin(message.from_user.id):
                self._home(message.chat.id)
                return
            self.core.send(
                message.chat.id,
                "⛔ <b>Нет доступа</b>\n\n"
                "Ваш Telegram ID: <code>" + str(sender_id) + "</code>\n"
                "Укажите именно его в переменной <code>ADMIN_ID</code> на Bothost.",
            )

        @self.bot.callback_query_handler(
            func=lambda call: str(getattr(call, "data", "")).startswith(("home:", "pm:", "cfg:"))
        )
        def callback(call: Any) -> None:
            if not self.core.is_admin(call.from_user.id):
                return
            data = str(call.data or "")
            self.core.answer(call)
            if data == "home:start":
                self._home(call.message.chat.id)
                return
            if data == "home:primary":
                self._open_primary(call.message.chat.id)
                return
            if data.startswith("home:plugin:"):
                info = self._info_by_index(data.rsplit(":", 1)[-1])
                if info is None:
                    self._home(call.message.chat.id)
                    return
                self._open_plugin(info, call.message.chat.id)
                return
            if data == "home:plugins":
                self._plugins_screen(call)
                return
            if data == "cfg:playerok":
                self._playerok_screen(call)
                return
            if data == "cfg:playerok:start":
                self._begin_playerok_setup(call.message.chat.id)
                return
            if data == "cfg:playerok:ask_clear":
                keyboard = types.InlineKeyboardMarkup(row_width=1)
                keyboard.add(types.InlineKeyboardButton("🗑 Да, удалить", callback_data="cfg:playerok:clear"))
                keyboard.add(types.InlineKeyboardButton("❌ Отмена", callback_data="cfg:playerok"))
                self.core.edit(
                    call,
                    "⚠️ Удалить сохранённое подключение Playerok из <code>/app/data</code>?",
                    keyboard,
                )
                return
            if data == "cfg:playerok:clear":
                self.core.clear_playerok_connection()
                self.core.clear_state(call.message.chat.id)
                self.core.send(call.message.chat.id, "🗑 Подключение Playerok удалено.")
                self._playerok_screen(call.message.chat.id)
                return
            if data == "pm:add":
                self.core.set_state(call.message.chat.id, "plugin_upload", {})
                cancel = types.InlineKeyboardMarkup()
                cancel.add(types.InlineKeyboardButton("❌ Отмена", callback_data="home:plugins"))
                self.core.send(
                    call.message.chat.id,
                    "Пришлите новый файл плагина <code>.py</code>.\n"
                    "Если UUID совпадёт, старая версия будет заменена автоматически.",
                    cancel,
                )
                return
            if data.startswith("pm:card:"):
                self._plugin_card(call, int(data.rsplit(":", 1)[-1]))
                return
            if data.startswith("pm:open:"):
                info = self._info_by_index(data.rsplit(":", 1)[-1])
                if info is None:
                    self._plugins_screen(call)
                    return
                try:
                    self._open_plugin(info, call.message.chat.id)
                except Exception as exc:
                    self.core.send(call.message.chat.id, "❌ " + self.core.esc(exc))
                return
            if data.startswith("pm:update:"):
                index = data.rsplit(":", 1)[-1]
                info = self._info_by_index(index)
                if info is None:
                    self._plugins_screen(call)
                    return
                self.core.set_state(
                    call.message.chat.id,
                    "plugin_upload",
                    {"expected_uuid": info.uuid, "return_index": index},
                )
                cancel = types.InlineKeyboardMarkup()
                cancel.add(types.InlineKeyboardButton("❌ Отмена", callback_data="pm:card:" + index))
                self.core.send(
                    call.message.chat.id,
                    "🔄 Пришлите новый файл <code>.py</code> для <b>" + self.core.esc(info.name) + "</b>.\n"
                    "Обновление будет принято только при совпадении UUID <code>" + self.core.esc(info.uuid) + "</code>.",
                    cancel,
                )
                return
            if data.startswith("pm:toggle:"):
                index = data.rsplit(":", 1)[-1]
                info = self._info_by_index(index)
                if info is None:
                    self._plugins_screen(call)
                    return
                try:
                    updated = self.plugins.set_enabled(info.uuid, not info.enabled)
                    action = "включён" if updated.enabled else "выключен"
                    self.core.send(
                        call.message.chat.id,
                        ("▶️ " if updated.enabled else "⏸ ") + "Плагин <b>" + self.core.esc(updated.name)
                        + "</b> " + action + ". Перезапускаю бота…",
                    )
                    self.core.request_restart()
                except Exception as exc:
                    self.core.send(call.message.chat.id, "❌ Не удалось изменить статус: " + self.core.esc(exc))
                return
            if data.startswith("pm:ask:"):
                index = data.rsplit(":", 1)[-1]
                info = self._info_by_index(index)
                if info is None:
                    self._plugins_screen(call)
                    return
                keyboard = types.InlineKeyboardMarkup(row_width=1)
                keyboard.add(types.InlineKeyboardButton("🗑 Да, удалить", callback_data="pm:delete:" + index))
                keyboard.add(types.InlineKeyboardButton("❌ Отмена", callback_data="home:plugins"))
                self.core.edit(
                    call,
                    "⚠️ Удалить плагин <b>" + self.core.esc(info.name) + "</b>?\n\n"
                    "Его данные в <code>/app/data</code> останутся для следующей версии.",
                    keyboard,
                )
                return
            if data.startswith("pm:delete:"):
                info = self._info_by_index(data.rsplit(":", 1)[-1])
                if info is None:
                    self._plugins_screen(call)
                    return
                try:
                    removed = self.plugins.delete(info.uuid)
                    self.core.send(
                        call.message.chat.id,
                        "🗑 Плагин удалён: <b>" + self.core.esc(removed.name) + "</b>. Перезапускаю бота…",
                    )
                    self.core.request_restart()
                except Exception as exc:
                    self.core.send(call.message.chat.id, "❌ Не удалось удалить: " + self.core.esc(exc))

        @self.bot.message_handler(content_types=["document"])
        def document(message: Any) -> None:
            if not self.core.is_admin(message.from_user.id):
                return
            state, payload = self.core.get_state(message.chat.id)
            if state != "plugin_upload":
                return
            document_obj = message.document
            filename = str(getattr(document_obj, "file_name", "") or "plugin.py")
            try:
                file_info = self.bot.get_file(document_obj.file_id)
                content = self.bot.download_file(file_info.file_path)
                expected_uuid = str(payload.get("expected_uuid") or "")
                if expected_uuid:
                    temporary = self.plugins.plugins_dir / ("_check_" + str(message.message_id) + ".py")
                    temporary.write_bytes(content)
                    try:
                        actual_uuid, _, _, _, _ = self.plugins._metadata(temporary)
                    finally:
                        temporary.unlink(missing_ok=True)
                    if actual_uuid != expected_uuid:
                        raise ValueError(
                            "UUID не совпадает: ожидался " + expected_uuid + ", получен " + actual_uuid
                        )
                info = self.plugins.install(filename, content)
                self.core.clear_state(message.chat.id)
                self.core.send(
                    message.chat.id,
                    "✅ Установлен <b>" + self.core.esc(info.name) + "</b> v" + self.core.esc(info.version)
                    + ". Перезапускаю бота…",
                )
                self.core.request_restart()
            except Exception as exc:
                self.core.send(message.chat.id, "❌ Плагин не установлен: " + self.core.esc(exc))

        @self.bot.message_handler(
            content_types=["text"],
            func=lambda message: self.core.get_state(message.chat.id)[0].startswith("playerok_"),
        )
        def playerok_input(message: Any) -> None:
            if self.core.is_admin(message.from_user.id):
                self._handle_playerok_input(message)

    def run(self) -> None:
        try:
            me = self.bot.get_me()
            username = str(getattr(me, "username", "") or "без username")
            self.bot.remove_webhook()
            log.info(
                "Telegram подключён: @%s. ADMIN_ID=%s. Плагины: %s",
                username,
                self.core.admin_id,
                len(self.plugins.infos),
            )
        except Exception:
            log.exception("Не удалось подключиться к Telegram. Проверьте BOT_TOKEN и доступ Bothost к Telegram")
            raise
        self.bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)


if __name__ == "__main__":
    App().run()
