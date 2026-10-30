"""Mihomo control API client that never uses the outbound proxy."""
import json
from urllib.request import ProxyHandler, Request, build_opener


OPENER = build_opener(ProxyHandler({}))


def request(api: str, path: str, method: str = "GET", payload: dict | None = None, timeout: int = 30):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else {}
    with OPENER.open(Request(api + path, data=data, method=method, headers=headers), timeout=timeout) as response:
        body = response.read()
    return json.loads(body) if body else None
