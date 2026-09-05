from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

from .paths import runtime_dir, sde_dir, source_root
from .version import VERSION

ROOT = source_root()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    eve_client_id: str = ""
    eve_redirect_uri: str = "http://localhost:8000/api/auth/callback"
    app_base_url: str = "http://localhost:8000"
    user_agent: str = f"4CRABS-EVE-Skill-Optimizer/{VERSION}"

    esi_base_url: str = "https://esi.evetech.net"
    sso_metadata_url: str = "https://login.eveonline.com/.well-known/oauth-authorization-server"
    sde_manifest_url: str = "https://developers.eveonline.com/static-data/tranquility/latest.jsonl"
    sde_base_url: str = "https://developers.eveonline.com/static-data/tranquility"

    sde_dir: Path = sde_dir()
    runtime_dir: Path = runtime_dir()


settings = Settings()
