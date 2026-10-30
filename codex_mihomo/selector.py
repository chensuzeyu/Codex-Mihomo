#!/usr/bin/env python3
"""Choose healthy Codex exits without chasing each latency fluctuation."""
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import re
import signal
import time

from .api import request as api_request
from .settings import Settings


HEALTH_URL = "https://chatgpt.com/backend-api/codex/responses"
WINDOW = 20
QUARANTINE = 180
RECOVERY = 3
HOLD = 120
FRESH = 40


def timestamp(value):
    # Go trims fractional trailing zeros. Python 3.10 requires 3 or 6 digits.
    normalized = re.sub(r"\.\d+", lambda match: "." + match.group()[1:][:6].ljust(6, "0"),
                        value.replace("Z", "+00:00"))
    return datetime.fromisoformat(normalized).timestamp()


def history(node):
    # Never mix generic Internet checks with the configured health URL.
    return node.get("extra", {}).get(HEALTH_URL, {}).get("history", [])


def update(record, samples):
    for item in sorted(samples, key=lambda sample: sample["time"]):
        stamp = timestamp(item["time"])
        if stamp <= record.get("last", 0):
            continue
        delay = item["delay"]
        record["last"] = stamp
        record.setdefault("samples", []).append([stamp, delay])
        record["samples"] = record["samples"][-WINDOW:]
        record["streak"] = record.get("streak", 0) + 1 if delay else 0
        if not delay:
            record["until"] = stamp + QUARANTINE
    return record


def eligible(record, now):
    samples = record.get("samples", [])
    return bool(samples and samples[-1][1] > 0 and now - record["last"] <= FRESH
                and now >= record.get("until", 0)
                and (not record.get("until") or record.get("streak", 0) >= RECOVERY))


def score(record):
    samples = record["samples"]
    good = sorted(delay for _, delay in samples if delay)
    failures = sum(delay == 0 for _, delay in samples) / len(samples)
    median = good[len(good) // 2]
    p90 = good[min(len(good) - 1, math.ceil(len(good) * .9) - 1)]
    return failures * 10000 + median + .5 * (p90 - median)


def choose(state, nodes, current, now):
    records = state.setdefault("nodes", {})
    # Removed nodes cannot survive a provider refresh or switch in local state.
    for name in list(records):
        if name not in nodes:
            del records[name]
    for name, node in nodes.items():
        if records.get(name, {}).get("identity") != node.get("id"):
            records[name] = {"identity": node.get("id")}
        update(records.setdefault(name, {}), history(node))
    ready = [name for name in nodes if eligible(records[name], now)]
    if not ready:
        # If the whole pool failed, do not wait out quarantine after proven recovery.
        recovered = [name for name in nodes if records[name].get("streak", 0) >= RECOVERY
                     and now - records[name].get("last", 0) <= FRESH]
        if recovered:
            return min(recovered, key=lambda name: (score(records[name]), name)), "emergency-proven-recovery"
        return None, "no-healthy-candidate"
    best = min(ready, key=lambda name: (score(records[name]), name))
    if current not in ready:
        return best, "current-unhealthy-or-cooling"
    if now - state.get("switched_at", 0) < HOLD:
        return current, "hold"
    old_score, new_score = score(records[current]), score(records[best])
    if old_score - new_score >= max(150, old_score * .2):
        return best, "sustained-quality-improvement"
    return current, "stable"


class Controller:
    def __init__(self, api):
        self.api = api.rstrip("/")

    def request(self, path, body=None):
        return api_request(self.api, path, "GET" if body is None else "PUT", body, timeout=5)


def save(path, state):
    temporary = path.with_suffix(".next")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def tick(controller, state, now, pin=None):
    proxies = controller.request("/proxies")["proxies"]
    group = proxies.get("OUTBOUND", {})
    if group.get("type") != "Selector":
        return "inactive", None
    provider = controller.request("/providers/proxies")["providers"].get("subscription", {})
    allowed = set(group.get("all", []))
    nodes = {node["name"]: node for node in provider.get("proxies", []) if node["name"] in allowed}
    current = group.get("now")
    selected, reason = choose(state, nodes, current, now)
    changes = []
    for name, record in state["nodes"].items():
        ready = eligible(record, now)
        if record.get("ready") != ready:
            changes.append({"name": name, "eligible": ready, "cooldown_until": record.get("until", 0)})
        record["ready"] = ready
    if pin in nodes and eligible(state["nodes"][pin], now):
        selected, reason = pin, "manual-pin-healthy"
    if selected and selected != current:
        controller.request("/proxies/OUTBOUND", {"name": selected})
        if controller.request("/proxies/OUTBOUND").get("now") != selected:
            raise RuntimeError("Selection readback mismatch")
        state["switched_at"] = now
        return "switch", {"old": current, "new": selected, "reason": reason,
                          "score": round(score(state["nodes"][selected]), 1), "health_changes": changes}
    return reason, {"health_changes": changes} if changes else None


def run(settings: Settings, once: bool = False, state_path: Path | None = None):
    global HEALTH_URL
    HEALTH_URL = settings.health_url
    os.umask(0o077)
    state_path = state_path or (settings.home / "selector-state.json")
    state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    controller = Controller(settings.api)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    stopped = False

    def stop(*_args):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    last_status = None
    with state_path.with_suffix(".lock").open("w", encoding="utf-8") as singleton:
        fcntl.flock(singleton, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while not stopped:
            try:
                with (state_path.parent / "apply.lock").open("a", encoding="utf-8") as apply_lock:
                    fcntl.flock(apply_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    try:
                        pin = json.loads((state_path.parent / "selector-pin.json").read_text(encoding="utf-8")).get("name")
                    except (OSError, ValueError):
                        pin = None
                    status, event = tick(controller, state, time.time(), pin)
                    save(state_path, state)
                if event or status != last_status:
                    print(json.dumps({"time": datetime.now(timezone.utc).isoformat(),
                                      "event": status, **(event or {})}, ensure_ascii=False), flush=True)
                last_status = status
            except BlockingIOError:
                pass
            except Exception as error:
                if last_status != type(error).__name__:
                    print(json.dumps({"time": datetime.now(timezone.utc).isoformat(),
                                      "event": "controller-error", "type": type(error).__name__,
                                      "detail": str(error)[:200]}, ensure_ascii=False), flush=True)
                last_status = type(error).__name__
            if once:
                break
            for _ in range(max(1, settings.health_interval)):
                if stopped:
                    break
                time.sleep(1)
