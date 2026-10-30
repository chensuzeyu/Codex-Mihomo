"""Per-host settings. Subscription URLs and node credentials are not settings."""
from dataclasses import dataclass
import os
from pathlib import Path
import re

import yaml


DEFAULT_EXCLUDE = (
    r"(?i)香港|🇭🇰|hong[ -]?kong|(?:^|[^a-z])hk(?:$|[^a-z])|剩余流量|套餐到期"
)
LOOPBACK = {"127.0.0.1", "::1"}


def repo_root() -> Path:
    # This locates the package checkout, not a workspace or data directory.
    return Path(__file__).resolve().parents[1]


def default_config_path() -> Path:
    return Path.home() / ".config" / "codex-mihomo" / "settings.yaml"


@dataclass
class Settings:
    home: Path
    mihomo_bin: Path
    config_path: Path
    bind_address: str = "127.0.0.1"
    mixed_port: int = 19082
    controller_port: int = 19083
    health_url: str = "https://chatgpt.com/backend-api/codex/responses"
    expected_status: int = 405
    health_interval: int = 10
    health_timeout_ms: int = 5000
    include_filter: str = ""
    exclude_filter: str = DEFAULT_EXCLUDE
    subscription_interval: int = 3600
    default_download_route: str = "BOOTSTRAP"
    provider_user_agent: str = "clash.meta"
    ipv6: bool = False
    log_level: str = "warning"
    codex_bin: str = "codex"
    codex_home: str = ""
    service_name: str = "codex-mihomo"
    supervisor_config: str = ""
    supervisor_user: str = ""

    @property
    def api(self) -> str:
        return f"http://{self.bind_address}:{self.controller_port}"

    @property
    def proxy(self) -> str:
        return f"http://{self.bind_address}:{self.mixed_port}"

    @property
    def controller(self) -> str:
        return f"{self.bind_address}:{self.controller_port}"

    @classmethod
    def defaults(cls, home: Path | None = None, config_path: Path | None = None) -> "Settings":
        root = home or Path("~/.codex-mihomo").expanduser()
        return cls(home=root, mihomo_bin=root / "mihomo", config_path=config_path or default_config_path())


def _path(value, default: Path) -> Path:
    return Path(os.path.expanduser(str(value if value not in (None, "") else default)))


def _port(value, name: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError(name + " is outside 1-65535")
    return port


def _compile(pattern: str, name: str):
    if not pattern:
        return
    try:
        re.compile(pattern)
    except re.error as error:
        raise ValueError(name + " is not a valid regular expression") from error


def load_settings(path: Path | None = None) -> Settings:
    if path is not None:
        config_path = Path(path).expanduser()
    elif os.environ.get("CODEX_MIHOMO_CONFIG"):
        config_path = Path(os.environ["CODEX_MIHOMO_CONFIG"]).expanduser()
    else:
        config_path = default_config_path()
    data = {}
    if config_path.is_file():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError("Settings file must be a mapping")
        data = loaded
    if "subscription_url" in data or "proxies" in data:
        raise ValueError("Put subscription URLs in the runtime home via `provider register`, not in settings")

    home = _path(os.environ.get("CODEX_MIHOMO_HOME", data.get("home")), Path("~/.codex-mihomo"))
    binary = _path(os.environ.get("CODEX_MIHOMO_BIN", data.get("mihomo_bin")), home / "mihomo")
    bind_address = str(data.get("bind_address", "127.0.0.1"))
    if bind_address not in LOOPBACK:
        raise ValueError("bind_address must be 127.0.0.1 or ::1")
    route = str(data.get("default_download_route", "BOOTSTRAP"))
    if route not in ("BOOTSTRAP", "DIRECT"):
        raise ValueError("default_download_route must be BOOTSTRAP or DIRECT")
    service_name = str(data.get("service_name", "codex-mihomo"))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,40}", service_name):
        raise ValueError("service_name contains unsupported characters")
    include_filter = str(data.get("include_filter", "") or "")
    exclude_filter = str(data.get("exclude_filter", DEFAULT_EXCLUDE))
    _compile(include_filter, "include_filter")
    _compile(exclude_filter, "exclude_filter")
    return Settings(
        home=home,
        mihomo_bin=binary,
        config_path=config_path,
        bind_address=bind_address,
        mixed_port=_port(data.get("mixed_port", 19082), "mixed_port"),
        controller_port=_port(data.get("controller_port", 19083), "controller_port"),
        health_url=str(data.get("health_url", "https://chatgpt.com/backend-api/codex/responses")),
        expected_status=int(data.get("expected_status", 405)),
        health_interval=int(data.get("health_interval", 10)),
        health_timeout_ms=int(data.get("health_timeout_ms", 5000)),
        include_filter=include_filter,
        exclude_filter=exclude_filter,
        subscription_interval=int(data.get("subscription_interval", 3600)),
        default_download_route=route,
        provider_user_agent=str(data.get("provider_user_agent", "clash.meta")),
        ipv6=bool(data.get("ipv6", False)),
        log_level=str(data.get("log_level", "warning")),
        codex_bin=str(data.get("codex_bin", "codex") or "codex"),
        codex_home=str(data.get("codex_home", "") or ""),
        service_name=service_name,
        supervisor_config=str(data.get("supervisor_config", "") or ""),
        supervisor_user=str(data.get("supervisor_user", "") or ""),
    )
