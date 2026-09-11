from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _default_data_dir() -> Path:
    if Path("/app").exists():
        return Path("/app/data")
    return (Path.cwd() / "data").resolve()


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_id: int
    data_dir: Path

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("BOT_TOKEN", "").strip()
        admin_raw = os.getenv("ADMIN_ID", "").strip()
        if not token:
            raise RuntimeError("Не задана переменная BOT_TOKEN")
        if not admin_raw.isdigit():
            raise RuntimeError("ADMIN_ID должен быть числовым Telegram ID владельца")
        data_dir = _default_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            bot_token=token,
            admin_id=int(admin_raw),
            data_dir=data_dir,
        )
