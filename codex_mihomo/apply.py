"""Validate, hot-reload, and probe a candidate config, rolling back on failure."""
import fcntl
import os
from pathlib import Path
import subprocess
import time

import yaml

from .api import request
from .provider import probe, save_bytes, validate
from .settings import Settings


def reload_config(settings: Settings, path: Path, old_node: str | None = None) -> None:
    request(settings.api, "/configs?force=true", "PUT", {"path": str(path)})
    for name in ("BOOTSTRAP", "OUTBOUND"):
        item = request(settings.api, "/proxies/" + name)
        if isinstance(item, dict) and item.get("type") == "Selector":
            if old_node in item.get("all", []):
                request(settings.api, "/proxies/" + name, "PUT", {"name": old_node})
        else:
            request(settings.api, "/proxies/" + name, "DELETE")


def restart_service(settings: Settings) -> None:
    if not settings.supervisor_config:
        raise RuntimeError("Hot reload failed and supervisor_config is unset; restart the proxy process")
    subprocess.run(
        ["supervisorctl", "-c", settings.supervisor_config, "restart", settings.service_name],
        check=True,
    )


def apply_candidate(settings: Settings) -> None:
    os.umask(0o077)
    home = settings.home
    current, candidate, previous = [home / name for name in ("config.yaml", "config.next.yaml", "config.previous.yaml")]
    with (home / "apply.lock").open("w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        content = candidate.read_bytes()
        config = yaml.safe_load(content)
        if config["mixed-port"] != settings.mixed_port:
            raise AssertionError("mixed-port does not match settings")
        if config["bind-address"] != settings.bind_address or config["allow-lan"] is not False:
            raise AssertionError("listener must stay on the configured loopback address")
        if config["external-controller"] != settings.controller:
            raise AssertionError("external-controller does not match settings")
        if config["proxy-providers"]["subscription"]["proxy"] not in ("BOOTSTRAP", "DIRECT"):
            raise AssertionError("subscription download route must be BOOTSTRAP or DIRECT")
        if not config["proxies"] or "MATCH,OUTBOUND" not in config["rules"]:
            raise AssertionError("config has no outbound rule")
        validate(settings, config)
        old = current.read_bytes()
        old_group = request(settings.api, "/proxies/OUTBOUND")
        old_node = old_group.get("now") if isinstance(old_group, dict) else None
        save_bytes(previous, old)
        save_bytes(current, content)
        try:
            reload_config(settings, current, old_node)
            for _attempt in range(4):
                if probe(settings, settings.mixed_port):
                    candidate.unlink(missing_ok=True)
                    print(f"Config hot-reloaded; health check HTTP {settings.expected_status}; previous config retained")
                    return
                time.sleep(2)
            raise RuntimeError("Health check probe failed")
        except Exception:
            save_bytes(current, old)
            try:
                reload_config(settings, current, old_node)
            except Exception:
                restart_service(settings)
            print("New configuration failed; previous configuration restored")
            raise
