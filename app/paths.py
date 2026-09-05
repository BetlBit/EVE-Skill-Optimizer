from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "EveSkillOptimizer"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def source_root() -> Path:
    return Path(__file__).resolve().parents[1]


def app_bundle_dir() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return source_root()


def app_package_dir() -> Path:
    return app_bundle_dir() / "app"


def resource_path(*parts: str) -> Path:
    return app_bundle_dir().joinpath(*parts)


def user_data_dir(*, frozen: bool | None = None, local_appdata: str | None = None) -> Path:
    frozen = is_frozen() if frozen is None else frozen
    override = os.environ.get("EVE_SKILL_OPTIMIZER_USER_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if not frozen:
        return source_root() / "data"
    base = local_appdata or os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / APP_NAME


def runtime_dir(*, frozen: bool | None = None, local_appdata: str | None = None) -> Path:
    return user_data_dir(frozen=frozen, local_appdata=local_appdata) / "runtime"


def logs_dir(*, frozen: bool | None = None, local_appdata: str | None = None) -> Path:
    if frozen is None and not is_frozen() and not os.environ.get("EVE_SKILL_OPTIMIZER_USER_DATA_DIR"):
        return source_root() / "data" / "logs"
    return user_data_dir(frozen=frozen, local_appdata=local_appdata) / "logs"


def sde_dir(*, frozen: bool | None = None, local_appdata: str | None = None) -> Path:
    return user_data_dir(frozen=frozen, local_appdata=local_appdata) / "sde"


def cache_dir(*, frozen: bool | None = None, local_appdata: str | None = None) -> Path:
    return user_data_dir(frozen=frozen, local_appdata=local_appdata) / "cache"


def settings_dir(*, frozen: bool | None = None, local_appdata: str | None = None) -> Path:
    return user_data_dir(frozen=frozen, local_appdata=local_appdata) / "settings"


def ensure_runtime_dirs() -> None:
    for path in (runtime_dir(), logs_dir(), sde_dir(), cache_dir(), settings_dir()):
        path.mkdir(parents=True, exist_ok=True)
