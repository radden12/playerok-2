from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
import types as pytypes
import unittest
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

from PIL import Image


class Button:
    def __init__(self, text, callback_data=None):
        self.text = text
        self.callback_data = callback_data


class Keyboard:
    def __init__(self, row_width=1):
        self.row_width = row_width
        self.keyboard = []

    def add(self, *buttons):
        for button in buttons:
            self.keyboard.append([button])

    def row(self, *buttons):
        self.keyboard.append(list(buttons))


telebot = pytypes.ModuleType("telebot")
telebot.TeleBot = object
telebot.types = SimpleNamespace(InlineKeyboardButton=Button, InlineKeyboardMarkup=Keyboard)
sys.modules.setdefault("telebot", telebot)
sys.modules.setdefault("telebot.types", telebot.types)

requests = pytypes.ModuleType("requests")
requests.RequestException = Exception
requests.Session = object
requests.get = None
sys.modules.setdefault("requests", requests)


class ItemDealStatuses(Enum):
    PAID = 0
    SENT = 1


playerokapi = pytypes.ModuleType("playerokapi")
playerokapi_enums = pytypes.ModuleType("playerokapi.enums")
playerokapi_enums.ItemDealStatuses = ItemDealStatuses
playerokapi_misc = pytypes.ModuleType("playerokapi.misc")
playerokapi_misc.PERSISTED_QUERIES = {"item": "hash"}
playerokapi_misc.QUERIES = {"updateItem": "mutation"}
sys.modules.setdefault("playerokapi", playerokapi)
sys.modules.setdefault("playerokapi.enums", playerokapi_enums)
sys.modules.setdefault("playerokapi.misc", playerokapi_misc)


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PATH = ROOT / "bundled_plugins" / "KOSell_Playerok_Clean_v1_0_0.py"
spec = importlib.util.spec_from_file_location("clean_plugin_test", PLUGIN_PATH)
plugin = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(plugin)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeAccount:
    def __init__(self, forbid_first=False):
        self.base_url = "https://playerok.test"
        self.id = "owner"
        self.created = []
        self.removed = []
        self.published = []
        self.updated_deals = []
        self.sent = []
        self.items = {}
        self.forbid_first = forbid_first
        self.messages = []

    def create_item(self, **kwargs):
        path = kwargs["attachments"][0]
        assert isinstance(path, str)
        assert Path(path).is_file()
        item_id = "item-" + str(len(self.created) + 1)
        forbidden = self.forbid_first and not self.created
        self.items[item_id] = {
            "id": item_id,
            "attachments": [{"id": "att-" + item_id}],
            "isAttachmentsForbidden": forbidden,
        }
        self.created.append(kwargs)
        return SimpleNamespace(id=item_id, attachments=[SimpleNamespace(id="att-" + item_id)])

    def request(self, method, url, headers, payload=None, files=None):
        variables = plugin.json.loads(payload["variables"])
        return FakeResponse({"data": {"item": self.items.get(variables["id"], {})}})

    def get_item_priority_statuses(self, item_id, price):
        return [SimpleNamespace(id="free", price=0)]

    def publish_item(self, item_id, priority_id):
        self.published.append((item_id, priority_id))
        return True

    def remove_item(self, item_id):
        self.removed.append(item_id)
        self.items.pop(item_id, None)
        return True

    def get_chat_messages(self, chat_id, count=24):
        return SimpleNamespace(messages=list(self.messages))

    def send_message(self, chat_id, text):
        self.sent.append((str(chat_id), text))

    def update_deal(self, deal_id, status):
        self.updated_deals.append((deal_id, status))


class FakeCore:
    def __init__(self, root: Path, account=None):
        self.root = root
        self._account = account or FakeAccount()
        self.admin_id = 1
        self.sent = []

    def plugin_data_dir(self, uuid):
        path = self.root / uuid
        path.mkdir(parents=True, exist_ok=True)
        return path

    def playerok_account(self):
        return self._account

    def send(self, chat_id, text, keyboard=None):
        self.sent.append((chat_id, text, keyboard))


class FakeKOSell:
    api_key = "key"

    def __init__(self):
        self.rent_calls = 0

    def rent(self, product_id, hours, key):
        self.rent_calls += 1
        return {
            "rental_uid": "rent-1",
            "steam_login": "login",
            "steam_password": "password",
            "expires_at": "2099-01-01T00:00:00+00:00",
        }

    def credentials(self, uid):
        return {}

    def code(self, uid):
        return {"code": "12345"}


class CleanPluginTests(unittest.TestCase):
    def setUp(self):
        self.original_sleep = plugin.time.sleep
        plugin.time.sleep = lambda seconds: None

    def tearDown(self):
        plugin.time.sleep = self.original_sleep

    def test_profit_formula(self):
        cfg = {"profit_percent": "10", "playerok_fee_percent": "20", "minimum_price": "1"}
        self.assertEqual(plugin._sale_price(plugin.Decimal("100"), cfg), plugin.Decimal("138"))

    def test_720_hours_is_capped_only_in_playerok_attribute(self):
        option = SimpleNamespace(
            field="durationHours", label="Срок аренды", value=0,
            value_range_limit=SimpleNamespace(min=1, max=168),
        )
        selected, attribute_hours = plugin._select_options(SimpleNamespace(options=[option]), 720)
        self.assertEqual(attribute_hours, 168)
        self.assertEqual(selected[0].value, 168)

    def test_slot_uses_real_file_and_keeps_real_month(self):
        with tempfile.TemporaryDirectory() as tmp:
            account = FakeAccount()
            core = FakeCore(Path(tmp), account)
            runtime = plugin.Runtime(core)
            runtime.cfg["api_key"] = "12345678"
            image_path = Path(tmp) / "cover.jpg"
            Image.new("RGB", (1200, 675), (20, 30, 40)).save(image_path, "JPEG")
            option = SimpleNamespace(
                field="durationHours", label="Срок аренды", value=0,
                value_range_limit=SimpleNamespace(min=1, max=168),
            )
            context = {
                "account": account,
                "category": SimpleNamespace(options=[option]),
                "category_id": "cat",
                "obtaining": SimpleNamespace(id="obtain"),
                "game_id": "game",
                "game_name": "Test Game",
                "category_name": "Аренда",
                "category_path": "Test Game → Аренда",
                "category_source": "exact",
            }
            old_fields = plugin._data_fields
            plugin._data_fields = lambda *args: []
            try:
                binding = plugin._create_slot(
                    runtime, {"id": 7, "name": "Test Game"}, 720,
                    plugin.Decimal("100"), context, [("kosell_original", image_path)], 1,
                )
            finally:
                plugin._data_fields = old_fields
            self.assertEqual(binding["hours"], 720)
            self.assertEqual(binding["playerok_attribute_hours"], 168)
            self.assertIn("1 месяц", account.created[0]["name"])
            self.assertIn("1 месяц", account.created[0]["description"])
            self.assertEqual(account.created[0]["attachments"], [str(image_path)])
            self.assertTrue(binding["image_verified"])

    def test_forbidden_first_image_is_removed_and_safe_variant_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            account = FakeAccount(forbid_first=True)
            runtime = plugin.Runtime(FakeCore(Path(tmp), account))
            first = Path(tmp) / "first.jpg"
            second = Path(tmp) / "second.jpg"
            Image.new("RGB", (1200, 675), "red").save(first, "JPEG")
            Image.new("RGB", (1200, 675), "blue").save(second, "JPEG")
            context = {
                "account": account, "category": SimpleNamespace(options=[]), "category_id": "cat",
                "obtaining": SimpleNamespace(id="obtain"), "category_path": "Steam → Аренда",
            }
            old_fields = plugin._data_fields
            plugin._data_fields = lambda *args: []
            try:
                result = plugin._create_slot(
                    runtime, {"id": 8, "name": "Game"}, 24, plugin.Decimal("100"), context,
                    [("kosell_original", first), ("generated_safe", second)], 1,
                )
            finally:
                plugin._data_fields = old_fields
            self.assertEqual(account.removed, ["item-1"])
            self.assertEqual(result["image_source"], "generated_safe")
            self.assertEqual(result["playerok_item_id"], "item-2")

    def test_image_repair_uses_added_attachments_graphql_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "repair.jpg"
            Image.new("RGB", (1200, 675), "green").save(image_path, "JPEG")

            class Account:
                base_url = "https://playerok.test"

                def request(self, method, url, headers, payload=None, files=None):
                    self.payload = payload
                    self.files = files
                    return FakeResponse({"data": {"updateItem": {"id": "item"}}})

            account = Account()
            plugin._update_item_image(account, "item", ["old-att"], image_path)
            mapping = plugin.json.loads(account.payload["map"])
            operations = plugin.json.loads(account.payload["operations"])
            self.assertEqual(mapping, {"1": ["variables.addedAttachments.0"]})
            self.assertEqual(operations["variables"]["input"]["removedAttachments"], ["old-att"])

    def test_paid_order_delivered_once_and_guard_requires_exact_word(self):
        with tempfile.TemporaryDirectory() as tmp:
            account = FakeAccount()
            runtime = plugin.Runtime(FakeCore(Path(tmp), account))
            kosell = FakeKOSell()
            runtime.cfg["api_key"] = "key"
            runtime._client = kosell
            record = {
                "deal_id": "deal-1", "chat_id": "chat-1", "product_id": 5,
                "product_name": "Game", "hours": 24, "status": "new",
            }
            runtime.attempt_rent(record)
            runtime.attempt_rent(record)
            self.assertEqual(kosell.rent_calls, 1)
            self.assertEqual(record["status"], "delivered")
            self.assertIn("login", account.sent[-1][1])
            account.messages = [
                SimpleNamespace(id="m1", text="код пожалуйста", user=SimpleNamespace(id="buyer")),
                SimpleNamespace(id="m2", text="код", user=SimpleNamespace(id="buyer")),
            ]
            runtime.poll_active_chats()
            guard_messages = [text for _, text in account.sent if text.startswith("🔐 Steam Guard")]
            self.assertEqual(guard_messages, ["🔐 Steam Guard: 12345"])

    def test_delete_worker_only_uses_linked_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            account = FakeAccount()
            runtime = plugin.Runtime(FakeCore(Path(tmp), account))
            runtime.cfg["bindings"] = [
                {"playerok_item_id": "ours-1"}, {"playerok_item_id": "ours-2"},
            ]
            runtime.cfg["delete_running"] = True
            plugin._delete_all_worker(runtime, 1)
            self.assertEqual(account.removed, ["ours-1", "ours-2"])
            self.assertEqual(runtime.cfg["bindings"], [])
            self.assertFalse(runtime.cfg["delete_running"])


class PluginManagerTests(unittest.TestCase):
    def test_start_keyboard_has_exactly_two_buttons(self):
        from playerok_minimal.app import App

        keyboard = App._home_keyboard()
        buttons = [button for row in keyboard.keyboard for button in row]
        self.assertEqual(
            [button.text for button in buttons],
            ["🎮 KOSell → Playerok", "🧩 Плагины: добавить / удалить"],
        )

    def test_same_uuid_replaces_old_file(self):
        from playerok_minimal.plugin_manager import PluginManager

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            core = SimpleNamespace(data_dir=root)
            bundled = root / "bundled"
            bundled.mkdir()
            manager = PluginManager(core, bundled)
            first = b'UUID="same"\nNAME="First"\nVERSION="1"\ndef register(core): pass\n'
            second = b'UUID="same"\nNAME="Second"\nVERSION="2"\ndef register(core): pass\n'
            manager.install("first.py", first)
            manager.install("second.py", second)
            paths = list(manager.plugins_dir.glob("*.py"))
            self.assertEqual([path.name for path in paths], ["second.py"])
            self.assertIn('VERSION="2"', paths[0].read_text())


if __name__ == "__main__":
    unittest.main()
