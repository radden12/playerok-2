from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any


class JsonStore:
    """Потокобезопасное JSON-хранилище с атомарной заменой файла."""

    def __init__(self, path: Path, defaults: dict[str, Any]):
        self.path = Path(path)
        self.defaults = defaults
        self.lock = threading.RLock()

    def load(self) -> dict[str, Any]:
        with self.lock:
            if not self.path.exists():
                value = json.loads(json.dumps(self.defaults, ensure_ascii=False))
                self.save(value)
                return value
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                value = {}
            merged = json.loads(json.dumps(self.defaults, ensure_ascii=False))
            if isinstance(value, dict):
                _deep_update(merged, value)
            return merged

    def save(self, value: dict[str, Any]) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            body = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
            temporary.write_text(body, encoding="utf-8")
            try:
                temporary.chmod(0o600)
            except OSError:
                pass
            os.replace(temporary, self.path)
            try:
                self.path.chmod(0o600)
            except OSError:
                pass


def _deep_update(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value
