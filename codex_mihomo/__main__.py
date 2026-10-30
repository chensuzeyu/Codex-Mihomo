"""Command line for the reusable Codex Mihomo proxy."""
import argparse
import getpass
import os
from pathlib import Path
import sys

from . import control
from .provider import (
    check, check_name, download, locked, profile_names, show, subscription_url,
    use, write_profile,
)
from .runtime import doctor, install, render_proxy_env, up
from .settings import load_settings, repo_root


def _public(error: BaseException) -> str:
    text = str(error)
    if "http://" in text or "https://" in text:
        return "request failed"
    return text[:200]


def _provider(settings, args) -> None:
    action = args.action
    if args.download_route and action not in ("refresh", "register", "preview"):
        raise SystemExit("--download-route is supported only with register, refresh, and preview")
    if action == "list":
        show(settings)
        return
    if action in ("register", "use", "check", "refresh") and not args.name:
        raise SystemExit("Choose a provider name. `all` is supported only for refresh and check.")
    if args.name == "all" and action not in ("refresh", "check"):
        raise SystemExit("`all` is supported only for refresh and check")
    names = profile_names(settings) if args.name == "all" else [check_name(args.name)] if args.name else []
    with locked(settings):
        if action == "preview":
            url = _url_from_args(settings, args)
            raw = args.profile_file.read_bytes() if args.profile_file else download(url, settings)
            from .provider import build, validate
            import yaml
            config = build(yaml.safe_load(raw), url, settings)
            if args.download_route:
                config["proxy-providers"]["subscription"]["proxy"] = args.download_route
            validate(settings, config)
            print("Preview passed. Run `provider register <name>` and then `provider use <name>`.")
            return
        if action == "register":
            url = _url_from_args(settings, args)
            raw = args.profile_file.read_bytes() if args.profile_file else download(url, settings)
            write_profile(settings, names[0], url, raw, args.download_route)
            return
        if action == "use":
            use(settings, names[0])
            return
        failed = []
        for name in names:
            try:
                if action == "refresh":
                    url_path = settings.home / "profiles" / name / "url.txt"
                    url = url_path.read_text(encoding="utf-8").strip()
                    raw = args.profile_file.read_bytes() if args.profile_file else download(url, settings)
                    write_profile(settings, name, url, raw, args.download_route)
                else:
                    check(settings, name)
            except (Exception, SystemExit) as error:
                print(name + ": FAILED (" + type(error).__name__ + "); existing cache retained")
                failed.append(name)
                if isinstance(error, SystemExit) and not names:
                    raise
        if failed:
            raise RuntimeError("Failed providers: " + ", ".join(failed))


def _prompt_url() -> str:
    return getpass.getpass("Subscription URL (hidden): ")


def _url_from_args(settings, args) -> str:
    prompted = None if args.reuse_current_url or args.url_file else _prompt_url()
    return subscription_url(settings, args.reuse_current_url, args.url_file, prompted)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-mihomo", description="Loopback Mihomo proxy for Codex CLI")
    parser.add_argument("--config", type=Path, help="Settings file. Defaults to ~/.config/codex-mihomo/settings.yaml")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Write a private settings file from the example")
    init.add_argument("--force", action="store_true")

    commands.add_parser("up", help="Start Mihomo and the stable selector in one foreground command")
    commands.add_parser("doctor", help="Check ports, binary, and selector state without printing secrets")
    commands.add_parser("env", help="Print proxy exports for the current settings")

    selector = commands.add_parser("selector", help="Run only the stable selector")
    selector.add_argument("--once", action="store_true")

    managed = commands.add_parser("control", help="Status, nodes, pin, auto, or refresh")
    managed.add_argument("action", choices=["status", "nodes", "pin", "auto", "refresh"])
    managed.add_argument("node", nargs="?")

    provider = commands.add_parser("provider", help="Register, check, and switch subscription profiles")
    provider.add_argument("action", choices=["list", "preview", "register", "refresh", "check", "use"])
    provider.add_argument("name", nargs="?")
    provider.add_argument("--download-route", choices=["DIRECT", "BOOTSTRAP"])
    provider.add_argument("--url-file", type=Path)
    provider.add_argument("--profile-file", type=Path)
    provider.add_argument("--reuse-current-url", action="store_true")

    setup = commands.add_parser("install", help="Write runtime scripts and optionally register both services")
    setup.add_argument("--supervisor-config", type=Path)
    setup.add_argument("--systemd", choices=["user", "system"])
    setup.add_argument("--shell-rc", type=Path)
    setup.add_argument("--enable", action="store_true", help="With --systemd, enable and start the unit")
    return parser


def _init(settings, force: bool) -> None:
    destination = settings.config_path
    if destination.exists() and not force:
        raise SystemExit("Settings already exist: " + str(destination))
    example = (repo_root() / "settings.example.yaml").read_text(encoding="utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.with_suffix(".yaml.next")
    temporary.write_text(example, encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
    os.chmod(destination, 0o600)
    settings.home.mkdir(mode=0o700, parents=True, exist_ok=True)
    print("Wrote " + str(destination))
    print("Place the Mihomo executable at " + str(settings.mihomo_bin))


def main(argv=None) -> int:
    os.umask(0o077)
    try:
        args = _build_parser().parse_args(argv)
        settings = load_settings(args.config)
        if args.command == "init":
            _init(settings, args.force)
        elif args.command == "up":
            return up(settings)
        elif args.command == "doctor":
            return doctor(settings)
        elif args.command == "env":
            sys.stdout.write(render_proxy_env(settings))
        elif args.command == "selector":
            from .selector import run
            run(settings, once=args.once)
        elif args.command == "control":
            if args.action == "status":
                control.show_status(settings)
            elif args.action == "nodes":
                control.show_nodes(settings)
            elif args.action == "pin":
                if not args.node:
                    raise SystemExit("pin requires a node ID from `control nodes`, such as S2")
                control.pin(settings, args.node)
            elif args.action == "auto":
                control.automatic(settings)
            else:
                control.refresh(settings)
        elif args.command == "provider":
            _provider(settings, args)
        elif args.command == "install":
            install(settings, args.supervisor_config, args.systemd or "", args.shell_rc, args.enable)
        return 0
    except SystemExit as error:
        if isinstance(error.code, int):
            return error.code
        if error.code:
            print(error.code, file=sys.stderr)
        return 1
    except (AssertionError, RuntimeError, ValueError, OSError) as error:
        if os.environ.get("CODEX_MIHOMO_DEBUG"):
            raise
        print(type(error).__name__ + ": " + _public(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
