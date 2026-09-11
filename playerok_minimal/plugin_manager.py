from __future__ import annotations

import ast
import importlib.util
import json
import logging
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any


log = logging.getLogger("playerok_minimal.plugins")


@dataclass
class PluginInfo:
    uuid: str
    name: str
    version: str
    filename: str
    path: Path
    description: str = ""
    author: str = ""
    enabled: bool = True
    module: ModuleType | None = None
    loaded: bool = False
    error: str = ""


class PluginManager:
    def __init__(self, core: Any, bundled_dir: Path):
        self.core = core
        self.bundled_dir = Path(bundled_dir)
        self.plugins_dir = core.data_dir / "plugins"
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.bundled_state = core.data_dir / ".playerok_minimal_bundled_versions.json"
        self.disabled_state = core.data_dir / ".playerok_minimal_disabled_plugins.json"
        self.infos: dict[str, PluginInfo] = {}

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return default

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def _disabled_uuids(self) -> set[str]:
        values = self._read_json(self.disabled_state, [])
        return {str(value) for value in values} if isinstance(values, list) else set()

    def _save_disabled_uuids(self, values: set[str]) -> None:
        self._write_json(self.disabled_state, sorted(values))

    def seed_bundled_once(self) -> None:
        saved_versions = self._read_json(self.bundled_state, {})
        if not isinstance(saved_versions, dict):
            saved_versions = {}
        disabled = self._disabled_uuids()

        installed: dict[str, tuple[Path, str]] = {}
        for path in sorted(self.plugins_dir.glob("*.py")):
            try:
                uuid, _, version, _, _ = self._metadata(path)
                installed[uuid] = (path, version)
            except Exception:
                continue

        for source in sorted(self.bundled_dir.glob("*.py")):
            uuid, _, version, _, _ = self._metadata(source)
            current = installed.get(uuid)
            previously_bundled = str(saved_versions.get(uuid) or "")
            if uuid in disabled:
                saved_versions[uuid] = version
                continue
            if (
                current is not None
                and self._version_key(version) > self._version_key(current[1])
                and (not previously_bundled or previously_bundled == current[1])
            ):
                shutil.copy2(source, current[0])
            elif current is None and previously_bundled != version:
                target = self.plugins_dir / source.name
                shutil.copy2(source, target)
            saved_versions[uuid] = version

        self._write_json(self.bundled_state, saved_versions)

    @staticmethod
    def _version_key(value: str) -> tuple[int, ...]:
        numbers = tuple(int(part) for part in re.findall(r"\d+", str(value)))
        return numbers or (0,)

    @staticmethod
    def _metadata(path: Path) -> tuple[str, str, str, str, str]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        values: dict[str, str] = {}
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value_node = node.value
            if not isinstance(value_node, ast.Constant) or not isinstance(value_node.value, str):
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id in {"UUID", "NAME", "VERSION", "DESCRIPTION", "AUTHOR"}:
                    values[target.id] = value_node.value.strip()
        uuid = values.get("UUID", "")
        name = values.get("NAME", path.stem)
        version = values.get("VERSION", "0")
        description = values.get("DESCRIPTION", "")
        author = values.get("AUTHOR", "")
        if not uuid:
            raise ValueError("в плагине отсутствует строковая константа UUID")
        return uuid, name, version, description, author

    def discover(self) -> list[PluginInfo]:
        self.infos = {}
        disabled = self._disabled_uuids()
        for path in sorted(self.plugins_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                uuid, name, version, description, author = self._metadata(path)
                if uuid in self.infos:
                    raise ValueError("дублируется UUID " + uuid)
                self.infos[uuid] = PluginInfo(
                    uuid, name, version, path.name, path,
                    description=description, author=author, enabled=uuid not in disabled,
                )
            except Exception as exc:
                key = "invalid:" + path.name
                self.infos[key] = PluginInfo(
                    key, path.stem, "?", path.name, path, enabled=False, error=str(exc)
                )
        return list(self.infos.values())

    def load_all(self) -> list[PluginInfo]:
        self.discover()
        for info in self.infos.values():
            if info.error or not info.enabled:
                continue
            module_name = "playerok_user_plugin_" + "".join(char if char.isalnum() else "_" for char in info.uuid)
            try:
                spec = importlib.util.spec_from_file_location(module_name, info.path)
                if spec is None or spec.loader is None:
                    raise RuntimeError("не удалось создать модуль")
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                register = getattr(module, "register", None)
                if not callable(register):
                    raise RuntimeError("отсутствует register(core)")
                register(self.core)
                info.module = module
                info.loaded = True
            except Exception as exc:
                info.error = str(exc)
                log.exception("Не удалось загрузить плагин %s", info.filename)
        return list(self.infos.values())

    def get(self, uuid: str) -> PluginInfo | None:
        return self.infos.get(str(uuid))

    def primary(self) -> PluginInfo | None:
        info = self.get("kosell-playerok-clean-v1")
        if info and info.loaded:
            return info
        return next((row for row in self.infos.values() if row.loaded), None)

    def open(self, info: PluginInfo, chat_id: int) -> None:
        if not info.loaded or info.module is None:
            raise RuntimeError(info.error or "плагин не загружен")
        settings = getattr(info.module, "settings", None) or getattr(info.module, "settings_page", None)
        if not callable(settings):
            raise RuntimeError("у плагина отсутствует settings(core, chat_id)")
        settings(self.core, chat_id)

    def install(self, filename: str, content: bytes) -> PluginInfo:
        safe = Path(filename or "plugin.py").name
        if not safe.lower().endswith(".py"):
            raise ValueError("нужен файл .py")
        if len(content) > 2 * 1024 * 1024:
            raise ValueError("файл плагина больше 2 МБ")
        compile(content, safe, "exec")
        descriptor = tempfile.NamedTemporaryFile(
            # Discover ignores underscore-prefixed files, so an unfinished upload
            # can never be mistaken for another installed copy of the plugin.
            prefix="_upload-", suffix=".py", dir=self.plugins_dir, delete=False
        )
        temporary = Path(descriptor.name)
        try:
            descriptor.write(content)
            descriptor.flush()
            descriptor.close()
            uuid, name, version, description, author = self._metadata(temporary)
            current = next((row for row in self.discover() if row.uuid == uuid), None)
            target = self.plugins_dir / safe
            if target.exists() and (current is None or current.path != target):
                suffix = re.sub(r"[^a-zA-Z0-9_-]+", "-", uuid).strip("-")[:24] or "plugin"
                target = self.plugins_dir / (Path(safe).stem + "-" + suffix + ".py")
            os.replace(temporary, target)
            if current and current.path != target and current.path.exists():
                current.path.unlink()
            disabled = self._disabled_uuids()
            if uuid in disabled:
                disabled.remove(uuid)
                self._save_disabled_uuids(disabled)
            return PluginInfo(
                uuid, name, version, target.name, target,
                description=description, author=author, enabled=True,
            )
        finally:
            try:
                descriptor.close()
            except Exception:
                pass
            if temporary.exists():
                temporary.unlink()

    def delete(self, uuid: str) -> PluginInfo:
        info = self.get(uuid)
        if info is None:
            raise KeyError("плагин не найден")
        info.path.unlink(missing_ok=False)
        disabled = self._disabled_uuids()
        disabled.add(info.uuid)
        self._save_disabled_uuids(disabled)
        self.infos.pop(uuid, None)
        return info

    def set_enabled(self, uuid: str, enabled: bool) -> PluginInfo:
        info = self.get(uuid)
        if info is None:
            raise KeyError("плагин не найден")
        if info.uuid.startswith("invalid:"):
            raise ValueError("некорректный плагин нельзя включить")
        disabled = self._disabled_uuids()
        if enabled:
            disabled.discard(info.uuid)
        else:
            disabled.add(info.uuid)
        self._save_disabled_uuids(disabled)
        info.enabled = bool(enabled)
        return info
