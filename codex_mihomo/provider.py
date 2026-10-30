"""Build and store private provider profiles. Never print URLs or credentials."""
from datetime import datetime, timezone
import fcntl
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile

import yaml

from .settings import Settings


REGION_FILTER = (
    r"(?i)🇯🇵|🇸🇬|🇹🇼|日本|狮城|新加坡|台湾|台灣|臺灣|Japan|Singapore|Taiwan|"
    r"(?:^|[^A-Z])(?:JP|SG|TW)(?:$|[^A-Z])"
)
REGION = re.compile(REGION_FILTER)
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,40}")


def check_name(name: str) -> str:
    if name == "all" or not NAME.fullmatch(name or ""):
        raise SystemExit("Provider name must match [A-Za-z0-9][A-Za-z0-9_-]{0,40} and cannot be 'all'")
    return name


def profiles_root(settings: Settings) -> Path:
    return settings.home / "profiles"


def profile_names(settings: Settings) -> list[str]:
    root = profiles_root(settings)
    if not root.is_dir():
        return []
    return sorted(path.name for path in root.iterdir() if path.is_dir() and NAME.fullmatch(path.name))


def save_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    os.chmod(path, 0o600)


def dump(data) -> bytes:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode("utf-8")


def validate(settings: Settings, config: dict) -> None:
    settings.home.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not settings.mihomo_bin.is_file():
        raise RuntimeError("Mihomo binary is missing: " + str(settings.mihomo_bin))
    fd, temporary = tempfile.mkstemp(dir=settings.home, suffix=".yaml")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(dump(config))
        result = subprocess.run(
            [str(settings.mihomo_bin), "-t", "-d", str(settings.home), "-f", temporary],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
        )
        if result.returncode:
            raise RuntimeError("Mihomo config validation failed")
    finally:
        Path(temporary).unlink(missing_ok=True)


def build(source: dict, url: str, settings: Settings | None = None) -> dict:
    settings = settings or Settings.defaults()
    if not isinstance(source, dict) or not isinstance(source.get("proxies"), list):
        raise SystemExit("Subscription must be Clash/Mihomo YAML with a proxies list")
    include = re.compile(settings.include_filter) if settings.include_filter else None
    exclude = re.compile(settings.exclude_filter)
    proxies = []
    for node in source["proxies"]:
        if not isinstance(node, dict):
            continue
        name = str(node.get("name", ""))
        if include and not include.search(name):
            continue
        if exclude.search(name):
            continue
        copied = dict(node)
        copied["ip-version"] = "ipv4"
        copied["name"] = "BOOTSTRAP-" + name
        proxies.append(copied)
    if not proxies:
        raise SystemExit("No eligible nodes left after include/exclude filters")
    print(f"Subscription has {len(source['proxies'])} entries; selected {len(proxies)} eligible nodes")
    health = {
        "url": settings.health_url,
        "interval": settings.health_interval,
        "timeout": settings.health_timeout_ms,
        "expected-status": settings.expected_status,
    }
    return {
        "mixed-port": settings.mixed_port,
        "bind-address": settings.bind_address,
        "allow-lan": False,
        "ipv6": settings.ipv6,
        "mode": "rule",
        "log-level": settings.log_level,
        "external-controller": settings.controller,
        "proxy-providers": {
            "subscription": {
                "type": "http",
                "url": url,
                "path": "./providers/" + hashlib.sha256(url.encode()).hexdigest()[:16] + ".yaml",
                "header": {"User-Agent": [settings.provider_user_agent]},
                "interval": settings.subscription_interval,
                "proxy": settings.default_download_route,
                "exclude-filter": settings.exclude_filter,
                "override": {"ip-version": "ipv4"},
                "health-check": {"enable": True, "lazy": False, **health},
            }
        },
        "proxies": proxies,
        "proxy-groups": [
            {
                "name": "BOOTSTRAP",
                "type": "url-test",
                "tolerance": 150,
                "proxies": [node["name"] for node in proxies],
                "max-failed-times": 2,
                "lazy": True,
                **health,
            },
            {
                "name": "OUTBOUND",
                "type": "select",
                "use": ["subscription"],
                "max-failed-times": 2,
                "lazy": False,
                **health,
            },
        ],
        "rules": ["MATCH,OUTBOUND"],
    }


def download(url: str, settings: Settings) -> bytes:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": settings.provider_user_agent})
    routes = (
        ("configured proxy", {"http": settings.proxy, "https": settings.proxy}),
        ("direct", {}),
    )
    failures = []
    for name, proxies in routes:
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
            with opener.open(request, timeout=20) as response:
                content = response.read(2_000_001)
                if len(content) > 2_000_000:
                    raise ValueError("subscription exceeds 2 MB")
                print("Subscription downloaded through " + name)
                return content
        except urllib.error.HTTPError as error:
            failures.append(f"{name}: HTTP {error.code}")
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            failures.append(f"{name}: {type(error).__name__}")
    raise SystemExit("Subscription download failed (" + ", ".join(failures) + ")")


def subscription_url(settings: Settings, reuse_current: bool, url_file: Path | None, prompted: str | None) -> str:
    if reuse_current:
        current = yaml.safe_load((settings.home / "config.yaml").read_text(encoding="utf-8"))
        url = current["proxy-providers"]["subscription"]["url"]
    elif url_file:
        url = url_file.read_text(encoding="utf-8").strip()
    else:
        url = (prompted or "").strip()
    if not url.startswith(("http://", "https://")):
        raise SystemExit("Subscription URL must start with http:// or https://")
    return url


def source_config(settings: Settings, name: str, raw: bytes | None = None, url: str | None = None, route: str | None = None):
    folder = profiles_root(settings) / name
    url = url or (folder / "url.txt").read_text(encoding="utf-8").strip()
    raw = raw if raw is not None else (folder / "subscription.yaml").read_bytes()
    source = yaml.safe_load(raw)
    config = build(source, url, settings)
    metadata_path = folder / "metadata.json"
    saved = {}
    if metadata_path.exists():
        import json
        saved = json.loads(metadata_path.read_text(encoding="utf-8"))
    config["proxy-providers"]["subscription"]["proxy"] = route or saved.get("download_route", settings.default_download_route)
    validate(settings, config)
    return source, config


def write_profile(settings: Settings, name: str, url: str, raw: bytes, route: str | None = None) -> None:
    import json

    source, config = source_config(settings, name, raw, url, route)
    folder = profiles_root(settings) / name
    save_bytes(folder / "subscription.yaml", raw)
    save_bytes(folder / "url.txt", url.encode("utf-8"))
    save_bytes(folder / "config.yaml", dump(config))
    save_bytes(folder / "metadata.json", json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "nodes": len(source["proxies"]),
        "eligible_nodes": len(config["proxies"]),
        "selection_policy": "include-exclude-filters",
        "download_route": config["proxy-providers"]["subscription"]["proxy"],
    }).encode("utf-8"))
    print(name + ": cache refreshed and validated")


def current_name(settings: Settings) -> str:
    config_path = settings.home / "config.yaml"
    if not config_path.is_file():
        return "none"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    try:
        url = config["proxy-providers"]["subscription"]["url"]
    except (TypeError, KeyError):
        return "unregistered"
    for name in profile_names(settings):
        url_path = profiles_root(settings) / name / "url.txt"
        if url_path.is_file() and url_path.read_text(encoding="utf-8").strip() == url:
            return name
    return "unregistered"


def show(settings: Settings) -> None:
    import json

    print("Active provider:", current_name(settings))
    names = profile_names(settings)
    if not names:
        print("No cached providers. Register one with: provider register <name>")
        return
    for name in names:
        folder = profiles_root(settings) / name
        metadata_path = folder / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else {}
        print(name, "cached=" + str((folder / "subscription.yaml").is_file()), json.dumps(metadata))


def probe(settings: Settings, port: int) -> bool:
    result = subprocess.run(
        ["curl", "-sS", "-x", f"http://{settings.bind_address}:{port}", "-o", "/dev/null",
         "-w", "%{http_code}", "--connect-timeout", "8", "--max-time", "15", settings.health_url],
        capture_output=True, text=True, timeout=20,
    )
    return result.returncode == 0 and result.stdout.strip() == str(settings.expected_status)


def isolated_port(port: int) -> int:
    candidate = port + 100
    if candidate > 65535:
        candidate = port - 100
    if not 1 <= candidate <= 65535 or candidate == port:
        raise RuntimeError("No free port offset available for an isolated check")
    return candidate


def check(settings: Settings, name: str) -> None:
    import json
    import time
    import urllib.error
    import urllib.request

    source, config = source_config(settings, name)
    mixed = isolated_port(settings.mixed_port)
    controller = isolated_port(settings.controller_port)
    config["mixed-port"] = mixed
    config["external-controller"] = f"{settings.bind_address}:{controller}"
    with tempfile.TemporaryDirectory(prefix="probe-", dir=settings.home) as tmp:
        folder = Path(tmp)
        cache = folder / config["proxy-providers"]["subscription"]["path"]
        save_bytes(cache, dump({"proxies": source["proxies"]}))
        provider = config["proxy-providers"]["subscription"]
        provider["type"] = "file"
        for key in ("url", "proxy", "interval", "header"):
            provider.pop(key, None)
        save_bytes(folder / "config.yaml", dump(config))
        process = subprocess.Popen(
            [str(settings.mihomo_bin), "-d", tmp],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for _attempt in range(8):
                if process.poll() is not None:
                    raise RuntimeError("Isolated Mihomo exited (check for a port conflict)")
                time.sleep(2)
                try:
                    with opener.open(f"http://{settings.bind_address}:{controller}/proxies/OUTBOUND", timeout=5) as response:
                        group = json.load(response)
                    if probe(settings, mixed):
                        print(name + ": isolated health check returned the expected status; auto=" + group.get("now", ""))
                        return
                except (OSError, urllib.error.URLError):
                    pass
            raise RuntimeError("Isolated health check failed")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def use(settings: Settings, name: str) -> None:
    from .apply import apply_candidate

    source, config = source_config(settings, name)
    cache = settings.home / config["proxy-providers"]["subscription"]["path"]
    if not cache.exists():
        save_bytes(cache, dump({"proxies": source["proxies"]}))
    save_bytes(profiles_root(settings) / name / "config.yaml", dump(config))
    save_bytes(settings.home / "config.next.yaml", dump(config))
    apply_candidate(settings)
    print("Active provider:", current_name(settings))


def locked(settings: Settings):
    settings.home.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle = (settings.home / "provider-manager.lock").open("w", encoding="utf-8")
    fcntl.flock(handle, fcntl.LOCK_EX)
    return handle
