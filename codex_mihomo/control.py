"""Inspect the running loopback Mihomo instance and pin or release its exit node."""
import json
import os

from .api import request
from .settings import Settings


def group(settings: Settings, name: str):
    return request(settings.api, "/proxies/" + name)


def members(settings: Settings):
    bootstrap = group(settings, "BOOTSTRAP")["all"]
    outbound = [name for name in group(settings, "OUTBOUND")["all"] if name != "BOOTSTRAP"]
    return bootstrap, outbound


def pinned_name(settings: Settings) -> str:
    try:
        return json.loads((settings.home / "selector-pin.json").read_text(encoding="utf-8")).get("name") or "no"
    except (OSError, ValueError):
        return "no"


def show_status(settings: Settings) -> None:
    for name in ("BOOTSTRAP", "OUTBOUND"):
        item = group(settings, name)
        pinned = item.get("fixed") or "no"
        if name == "OUTBOUND" and item.get("type") == "Selector":
            pinned = pinned_name(settings)
        print(f"{name}: current={item.get('now')} pinned={pinned}")
    provider = request(settings.api, "/providers/proxies/subscription")
    print(f"subscription: nodes={len(provider.get('proxies', []))} updated={provider.get('updatedAt')}")


def show_nodes(settings: Settings) -> None:
    bootstrap, subscription = members(settings)
    print("Bootstrap nodes (from the active config):")
    for index, name in enumerate(bootstrap, 1):
        print(f"  B{index}: {name}")
    print("Subscription nodes (updated by the provider):")
    for index, name in enumerate(subscription, 1):
        print(f"  S{index}: {name}")


def _write_pin(settings: Settings, selected: str) -> None:
    temporary = settings.home / "selector-pin.next"
    temporary.write_text(json.dumps({"name": selected}), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, settings.home / "selector-pin.json")


def pin(settings: Settings, spec: str) -> None:
    bootstrap, subscription = members(settings)
    source, position = spec[0].upper(), spec[1:]
    if source not in ("B", "S") or not position.isdigit() or int(position) < 1:
        raise SystemExit("Use a node ID from 'nodes', such as B1 or S2")
    choices = bootstrap if source == "B" else subscription
    index = int(position) - 1
    if index >= len(choices):
        raise SystemExit("Node ID is out of range; run 'nodes' first")
    if source == "B":
        selected = choices[index].removeprefix("BOOTSTRAP-")
        if group(settings, "OUTBOUND")["type"] == "Selector":
            if selected not in subscription:
                raise SystemExit("Bootstrap node is absent from the live subscription; use an S node")
            request(settings.api, "/proxies/OUTBOUND", "PUT", {"name": selected})
        else:
            request(settings.api, "/proxies/BOOTSTRAP", "PUT", {"name": choices[index]})
            request(settings.api, "/proxies/OUTBOUND", "PUT", {"name": "BOOTSTRAP"})
    else:
        selected = choices[index]
        request(settings.api, "/proxies/OUTBOUND", "PUT", {"name": selected})
    if group(settings, "OUTBOUND")["type"] == "Selector":
        _write_pin(settings, selected)
    show_status(settings)


def automatic(settings: Settings) -> None:
    (settings.home / "selector-pin.json").unlink(missing_ok=True)
    if group(settings, "OUTBOUND")["type"] != "Selector":
        request(settings.api, "/proxies/OUTBOUND", "DELETE")
    request(settings.api, "/proxies/BOOTSTRAP", "DELETE")
    show_status(settings)


def refresh(settings: Settings) -> None:
    before = request(settings.api, "/providers/proxies/subscription").get("updatedAt")
    request(settings.api, "/providers/proxies/subscription", "PUT")
    provider = request(settings.api, "/providers/proxies/subscription")
    if not provider.get("proxies"):
        raise SystemExit("Subscription refresh returned no usable nodes")
    if provider.get("updatedAt") == before:
        raise SystemExit("Subscription refresh did not advance its update time")
    show_status(settings)
