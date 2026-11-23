from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from . import __version__, defaults, paths
from . import service as service_backend
from .catalog import build_catalog
from .emit import emit_all
from .health import (
    check_agents,
    diagnose,
    load_agents,
    port_open,
    proxy_skip,
    resolve_proxy_url,
)
from .merge import merge_profile, parse_target_map
from .probe import load_probes, probe_domains
from .profile import (
    apply_profile,
    discover_profiles,
    doctor_profile,
    list_backups,
    pick_profile,
    restore_profile,
)
from .sources import load_rules
from .surge import discover_surge_profiles
from .status import profile_status
from .verify import verify_outputs
from .watch import watch as watch_config


def _load_yaml(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: expected a mapping")
    return data


def _write_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def _latest_profile(discovered: dict[str, Any], command: str) -> str:
    """Resolve --latest: the client's selected profile, not the newest file."""
    picked = pick_profile(discovered["profiles"])
    if picked is None:
        raise SystemExit(f"{command}: no profiles discovered")
    return str(picked["path"])


def _health_proxy(value: str) -> str | None:
    """Resolve --proxy without deciding whether the route is alive."""
    if not value or value == "direct":
        return None
    if value != "auto":
        return value
    return resolve_proxy_url()


def _probe_proxy(value: str) -> str | None:
    """Resolve --probe-proxy: direct -> None, auto -> local mihomo if listening."""
    if not value or value == "direct":
        return None
    if value != "auto":
        return value
    proxy_url = resolve_proxy_url()
    if not proxy_url:
        return None
    return proxy_url if port_open(proxy_url) else None


def _build(args: argparse.Namespace) -> int:
    services_path = defaults.resolve("services", args.services)
    sources_path = defaults.resolve("sources", args.sources)
    services = _load_yaml(services_path).get("services", {})
    sources = _load_yaml(sources_path)
    if not services:
        raise SystemExit(f"{services_path}: no services")

    formats = tuple(
        ("mihomo", "sing-box", "surge") if "all" in args.formats else args.formats
    )
    # build always means "write here, now": only an explicit --out moves it.
    # Reading commands use paths.resolve_out(), which also knows the last build.
    out_dir = Path(args.out).expanduser() if args.out else Path.cwd() / "out"
    args.out = str(out_dir)
    os.makedirs(args.out, exist_ok=True)
    try:
        rules, source_meta = load_rules(sources, services, args.max_age_days)
    except FileNotFoundError as exc:
        raise SystemExit(f"build: {exc}") from exc
    catalog, coverage = build_catalog(rules, services, source_meta)

    probe_path = os.path.join(args.out, "probes.json")
    probe_results: dict[str, Any] | None = None
    if args.probe:
        probe_results = probe_domains(
            catalog["domains"],
            timeout=args.probe_timeout,
            workers=args.probe_workers,
            proxy_url=_probe_proxy(args.probe_proxy),
        )
        _write_json(probe_path, probe_results)
        catalog, coverage = build_catalog(
            rules, services, source_meta, probes=probe_results["domains"]
        )
    elif args.reuse_probes:
        probe_results = load_probes(probe_path)
        if probe_results is None:
            raise SystemExit(f"{probe_path}: not found; run build --probe first")
        catalog, coverage = build_catalog(
            rules, services, source_meta, probes=probe_results["domains"]
        )

    catalog_path = os.path.join(args.out, "catalog.json")
    coverage_path = os.path.join(args.out, "coverage.json")
    _write_json(catalog_path, catalog)
    _write_json(coverage_path, coverage)
    emit_all(
        catalog,
        coverage,
        services,
        args.out,
        strict=not args.raw,
        probes=probe_results,
        probes_path=probe_path if probe_results else None,
        probe_gate=args.probe_gate,
        asn_mode=args.asn_mode,
        formats=formats,
    )

    report: dict[str, Any] = {
        "ok": True,
        "strict": not args.raw,
        "formats": list(formats),
        "summary": coverage["summary"],
        "outputs": {
            "catalog": catalog_path,
            "coverage": coverage_path,
            "lock": os.path.join(args.out, "lock.json"),
        },
    }
    if "mihomo" in formats:
        report["outputs"]["providers"] = os.path.join(args.out, "AI-Rules")
        report["outputs"]["mihomo_config"] = os.path.join(
            args.out, "mihomo-config.yaml"
        )
    if "sing-box" in formats:
        report["outputs"]["singbox"] = os.path.join(args.out, "singbox")
    if "surge" in formats:
        report["outputs"]["surge"] = os.path.join(args.out, "surge")
    if os.path.exists(probe_path):
        report["outputs"]["probes"] = probe_path
    if not args.skip_verify:
        verification = verify_outputs(args.out, coverage, probes=probe_results)
        report["verification"] = verification
        report["ok"] = bool(verification["ok"])
        if verification["warnings"]:
            report["warnings"] = verification["warnings"]
    if report["ok"]:
        paths.record_build(args.out)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def _probe(args: argparse.Namespace) -> int:
    args.out = str(paths.resolve_out(args.out))
    catalog_path = os.path.join(args.out, "catalog.json")
    if not os.path.exists(catalog_path):
        raise SystemExit(f"{catalog_path}: not found; run build first")
    with open(catalog_path, encoding="utf-8") as fh:
        catalog = json.load(fh)
    domains = sorted(catalog.get("domains", {}))
    if args.limit:
        domains = domains[: args.limit]
    if not domains:
        raise SystemExit("no domains to probe")
    result = probe_domains(
        domains,
        timeout=args.timeout,
        workers=args.workers,
        proxy_url=_probe_proxy(args.proxy),
    )
    probe_path = os.path.join(args.out, "probes.json")
    _write_json(probe_path, result)
    print(
        json.dumps(
            {"ok": True, "summary": result["summary"], "path": probe_path},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _merge(args: argparse.Namespace) -> int:
    meta_path = paths.resolve_meta(args.meta)
    if not meta_path.is_file():
        raise SystemExit(
            f"merge: {meta_path} not found; run 'ai-rulec build' (or pass --meta)"
        )
    base = _load_yaml(args.base)
    meta = _load_yaml(str(meta_path))
    providers_dir = args.rules_dir or str(meta_path.parent / "AI-Rules")
    out_path = os.path.abspath(args.out)
    out_dir = os.path.dirname(out_path)
    try:
        mapping = parse_target_map(args.map)
        merged, report = merge_profile(
            base,
            meta,
            target=args.target,
            mapping=mapping,
            providers_dir=providers_dir,
            output_dir=out_dir,
            rules_position=args.rules_position,
        )
    except ValueError as exc:
        raise SystemExit(f"merge: {exc}") from exc
    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("# merged by ai-rulec\n")
        yaml.safe_dump(
            merged,
            fh,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
    report["output"] = out_path
    report["providers_dir"] = os.path.abspath(providers_dir)

    mihomo, mihomo_hint = paths.mihomo_binary(args.mihomo)
    if not mihomo:
        report["warnings"] = [*(report.get("warnings") or []), mihomo_hint]
    if mihomo:
        proc = subprocess.run(
            [mihomo, "-t", "-f", out_path],
            capture_output=True,
            text=True,
        )
        report["mihomo"] = {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "output": (proc.stdout + proc.stderr).strip()[-2000:],
        }
    else:
        report["mihomo"] = {
            "ok": None,
            "warning": f"runtime validation skipped: {mihomo_hint}",
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["mihomo"]["ok"] is not False else 1


def _discover(args: argparse.Namespace) -> int:
    result = discover_profiles(extra=args.root)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    profiles = result["profiles"]
    if not profiles:
        print("no mihomo/FlClash profiles found")
        for error in result["errors"]:
            print(f"  error: {error['path']}: {error['error']}")
        return 1
    for profile in profiles:
        managed = " managed" if profile["managed"] else ""
        active = " active" if profile.get("active") else ""
        print(f"{profile['path']}  [{profile['kind']}{active}{managed}]")
        print(
            "  "
            f"proxies={profile['proxies']} groups={profile['proxy_groups']} "
            f"rules={profile['rules']} target={profile['target_hint']}"
        )
    if result["errors"]:
        print(f"errors: {len(result['errors'])}")
    return 0


def _apply(args: argparse.Namespace) -> int:
    profile = args.profile
    if args.latest and not profile:
        profile = _latest_profile(
            discover_profiles(extra=args.profile_root), "apply"
        )
    if not profile:
        raise SystemExit("apply: pass a profile path or --latest")
    meta_path = paths.resolve_meta(args.meta)
    if not meta_path.is_file():
        raise SystemExit(
            f"apply: {meta_path} not found; run 'ai-rulec build' (or pass --meta)"
        )
    try:
        report = apply_profile(
            profile,
            str(meta_path),
            out_path=args.out,
            target=args.target,
            mapping=parse_target_map(args.map),
            rules_dir=args.rules_dir,
            preserve_existing_proxies=not args.reset_proxies,
            rules_position="before-match" if args.rules_last else "top",
            backup=not args.no_backup,
            copy_rules=not args.no_copy_rules,
            repair_cache=not args.no_repair_cache,
            verify_mihomo=args.mihomo,
            dry_run=args.dry_run,
        )
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"apply: {exc}") from exc

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(f"profile: {report['profile']}")
    print(f"output:  {report['output']}")
    if report.get("dry_run"):
        print("dry-run: no files written")
    print(
        "groups:  "
        f"+{report['groups_added']} ~{report['groups_replaced']}  "
        f"rules: +{report['rules_added']} -{report['rules_removed']}  "
        f"providers: +{report['providers_added']} ~{report['providers_replaced']}"
    )
    if report.get("backup"):
        for name, path in report["backup"].items():
            print(f"backup {name}: {path}")
    if report.get("rules_copied") is not None:
        print(f"rules copied: {report['rules_copied']}")
    mihomo = report.get("mihomo")
    if mihomo:
        if mihomo.get("ok") is None:
            print(f"mihomo: skipped ({mihomo.get('warning')})")
        else:
            print(f"mihomo: {'ok' if mihomo['ok'] else 'failed'}")
    return 0


def _surge(args: argparse.Namespace) -> int:
    from .surge import apply_surge

    args.out = str(paths.resolve_out(args.out))
    services = list(
        _load_yaml(defaults.resolve("services", args.services_file)).get("services", {})
    )
    if not services:
        coverage_path = os.path.join(args.out, "coverage.json")
        if os.path.exists(coverage_path):
            with open(coverage_path, encoding="utf-8") as fh:
                services = sorted(json.load(fh).get("services", {}))
    if not services:
        raise SystemExit("surge: no services (pass --services-file or build first)")

    profile = args.profile
    if not profile:
        discovered = discover_surge_profiles()
        if not discovered:
            raise SystemExit("surge: no Surge profile found; pass a path")
        profile = discovered[0]["path"]

    lists_dir = args.lists_dir or os.path.join(args.out, "surge", "AI-Rules")
    if not os.path.isdir(lists_dir):
        raise SystemExit(f"surge: {lists_dir} not found; run build --formats surge")

    try:
        report = apply_surge(
            profile,
            services,
            lists_dir=lists_dir,
            target=args.target,
            mapping=parse_target_map(args.map),
            rules_position="before-final" if args.rules_last else "top",
            dry_run=args.dry_run,
            backup=not args.no_backup,
        )
    except (ValueError, OSError) as exc:
        raise SystemExit(f"surge: {exc}") from exc

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"profile: {report['profile']}")
        print(f"target:  {report['target']}")
        print(
            f"groups:  -{report['groups_removed']} +{report['groups_added']}  "
            f"rules: +{report['rules_added']} ({report['rules_position']})"
        )
        if report.get("backup"):
            print(f"backup:  {report['backup']}")
        if report.get("lists_copied") is not None:
            print(f"lists:   {report['lists_copied']} copied to {report['rules_dir']}")
        check = report.get("check") or {}
        print(f"surge-cli: {check.get('status', 'skipped')}")
        if report.get("dry_run"):
            print("dry-run: no files written")
    return 0 if report["ok"] else 1


def _singbox(args: argparse.Namespace) -> int:
    from .singbox import apply_singbox, discover_configs

    args.out = str(paths.resolve_out(args.out))
    services = list(
        _load_yaml(defaults.resolve("services", args.services_file)).get("services", {})
    )
    if not services:
        coverage_path = os.path.join(args.out, "coverage.json")
        if os.path.exists(coverage_path):
            with open(coverage_path, encoding="utf-8") as fh:
                services = sorted(json.load(fh).get("services", {}))
    if not services:
        raise SystemExit("singbox: no services (pass --services-file or build first)")

    config = args.config
    if not config:
        discovered = discover_configs()
        if not discovered:
            raise SystemExit("singbox: no sing-box/Hiddify config found; pass a path")
        config = discovered[0]["path"]

    lists_dir = args.lists_dir or os.path.join(args.out, "singbox", "AI-Rules")
    if not os.path.isdir(lists_dir):
        raise SystemExit(f"singbox: {lists_dir} not found; run build --formats sing-box")

    try:
        report = apply_singbox(
            config,
            services,
            lists_dir=lists_dir,
            rules_dir=args.rules_dir,
            target=args.target,
            mapping=parse_target_map(args.map),
            absolute_paths=args.absolute_paths,
            dry_run=args.dry_run,
            backup=not args.no_backup,
        )
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"singbox: {exc}") from exc

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"config:   {report['config']}")
        print(f"target:   {report['target']} ({report['target_source']})")
        print(
            f"outbounds: -{report['outbounds_removed']} +{report['outbounds_added']}  "
            f"rule-sets: +{report['rule_sets_added']}  rules: +{report['rules_added']}"
        )
        if report.get("rules_copied") is not None:
            print(f"rules:    {report['rules_copied']} copied to {report['rules_dir']}")
        if report.get("backup"):
            print(f"backup:   {report['backup']}")
        check = report.get("check") or {}
        extra = f" (sanitized {len(check.get('sanitized') or [])})" if check.get("sanitized") else ""
        print(f"sing-box: {check.get('status', 'skipped')}{extra}")
        if report.get("dry_run"):
            print("dry-run: no files written")
    return 0 if report["ok"] else 1


def _singbox_watch(args: argparse.Namespace) -> int:
    from .singbox_watch import watch as watch_singbox

    args.out = str(paths.resolve_out(args.out))
    services = list(
        _load_yaml(defaults.resolve("services", args.services_file)).get("services", {})
    )
    if not services:
        coverage_path = os.path.join(args.out, "coverage.json")
        if os.path.exists(coverage_path):
            with open(coverage_path, encoding="utf-8") as fh:
                services = sorted(json.load(fh).get("services", {}))
    if not services:
        raise SystemExit("singbox-watch: no services (pass --services-file or build first)")

    lists_dir = args.lists_dir or os.path.join(args.out, "singbox", "AI-Rules")
    if not os.path.isdir(lists_dir):
        raise SystemExit(f"singbox-watch: {lists_dir} not found; run build --formats sing-box")

    return watch_singbox(
        args.config,
        services,
        lists_dir=lists_dir,
        rules_dir=args.rules_dir,
        target=args.target,
        mapping=parse_target_map(args.map),
        interval=args.interval,
        once=args.once,
        dry_run=args.dry_run,
        backup=not args.no_backup,
        force=args.force,
        state_path=args.state,
        json_output=args.json,
    )


def _rollback(args: argparse.Namespace) -> int:
    profile = args.profile
    if args.latest and not profile:
        profile = _latest_profile(
            discover_profiles(extra=args.profile_root), "rollback"
        )
    if not profile:
        raise SystemExit("rollback: pass a profile path or --latest")

    if args.list:
        result = list_backups(profile)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        print(f"profile: {profile}")
        for kind in ("profile", "rules"):
            backups = result[kind]
            print(f"{kind} backups: {len(backups)}")
            for path in backups:
                print(f"  {path}")
        return 0

    try:
        report = restore_profile(
            profile,
            backup_path=args.backup,
            rules_backup=args.rules_backup,
            restore_rules=not args.no_rules,
            verify_mihomo=args.mihomo,
            dry_run=args.dry_run,
        )
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"rollback: {exc}") from exc

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(f"profile:  {report['profile']}")
    print(f"restored: {report['restored_from']}")
    if report.get("rules_restored_from"):
        print(f"rules:    {report['rules_restored_from']}")
    elif args.no_rules:
        print("rules:    skipped")
    else:
        print("rules:    no backup found; left unchanged")
    if report.get("dry_run"):
        print("dry-run: no files written")
    mihomo = report.get("mihomo")
    if mihomo:
        if mihomo.get("ok") is None:
            print(f"mihomo: skipped ({mihomo.get('warning')})")
        else:
            print(f"mihomo: {'ok' if mihomo['ok'] else 'failed'}")
    for name, path in report.get("backup", {}).items():
        print(f"backup {name}: {path}")
    return 0


def _doctor(args: argparse.Namespace) -> int:
    profile = args.profile
    if args.latest and not profile:
        profile = _latest_profile(
            discover_profiles(extra=args.profile_root), "doctor"
        )
    if not profile:
        raise SystemExit("doctor: pass a profile path or --latest")

    try:
        report = doctor_profile(
            profile,
            rules_dir=args.rules_dir,
            fix=args.fix,
            dry_run=args.dry_run,
        )
    except (ValueError, OSError) as exc:
        raise SystemExit(f"doctor: {exc}") from exc

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    cache = report.get("cache") or {}
    summary = cache.get("summary") or {}
    print(f"profile: {report['profile']}")
    print(f"config:  {report.get('config')}")
    print(f"rules:   {report.get('rules_dir')}")
    print(
        "cache:   "
        f"{summary.get('ok', 0)}/{summary.get('managed', 0)} ok, "
        f"{summary.get('missing', 0)} missing, "
        f"{summary.get('mismatch', 0)} mismatch"
    )
    if report.get("repair"):
        repair = report["repair"]
        print(f"repaired: {len(repair.get('repaired', []))}")
        if repair.get("dry_run"):
            print("dry-run: no files written")
    for warning in report.get("warnings", []):
        print(f"warning: {warning}")
    for error in report.get("errors", []):
        print(f"error:   {error}")
    return 0 if report["ok"] else 1


def _watch(args: argparse.Namespace) -> int:
    profile = args.profile
    # --latest means "whichever profile the client has selected now".  The
    # watcher resolves that every cycle, so switching profiles in the client
    # does not leave it pinned to a stale subscription.
    follow_active = bool(args.latest and not profile)
    if follow_active:
        profile = _latest_profile(
            discover_profiles(extra=args.profile_root), "watch"
        )
    if not profile:
        raise SystemExit("watch: pass a profile path or --latest")

    try:
        result = watch_config(
            profile,
            follow_active=follow_active,
            meta_path=str(paths.resolve_meta(args.meta)) if args.reapply else None,
            target=args.target,
            mapping=parse_target_map(args.map),
            rules_dir=args.rules_dir,
            mihomo_path=args.mihomo,
            fix_cache=args.fix_cache,
            reapply=args.reapply,
            dry_run=args.dry_run,
            state_path=args.state,
            settle=args.settle,
            interval=args.interval,
            once=args.once,
            json_output=args.json,
            agents_path=defaults.resolve("agents", args.agents),
            agent_names=args.agent or None,
            check_agent_health=args.health,
            agents_interval=args.agents_interval,
            agents_timeout=args.agents_timeout,
        )
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"watch: {exc}") from exc
    except KeyboardInterrupt:
        return 0

    if not args.once:
        return 0
    if result is None:
        return 0
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"profile: {result.get('profile')}")
        print(f"config:  {result.get('config')}")
        print(f"action:  {result.get('action')}")
        print(f"ok:      {result.get('ok')}")
        cache = result.get("cache") or {}
        summary = cache.get("summary") or {}
        if summary:
            print(
                "cache:   "
                f"{summary.get('ok', 0)}/{summary.get('managed', 0)} ok, "
                f"{summary.get('missing', 0)} missing, "
                f"{summary.get('mismatch', 0)} mismatch"
            )
        if result.get("restart_required"):
            print(f"restart: required ({result.get('restart_reason')})")
        health = result.get("health") or {}
        if health.get("skipped"):
            print(f"agents:  skipped ({health['skipped']})")
        elif health.get("error"):
            print(f"agents:  error ({health['error']})")
        elif health.get("pending") and not health.get("summary"):
            print("agents:  pending (first sweep still running)")
        elif health.get("summary"):
            summary_h = health["summary"]
            print(
                "agents:  "
                f"{summary_h.get('ok', 0)} ok, "
                f"{summary_h.get('degraded', 0)} degraded, "
                f"{summary_h.get('down', 0)} down  via {health.get('proxy') or 'direct'}"
            )
            if health.get("down_agents"):
                print("down:    " + ", ".join(health["down_agents"]))
        for warning in result.get("warnings") or []:
            print(f"warning: {warning}")
        for error in result.get("errors") or []:
            print(f"error:   {error}")
    return 0 if result.get("ok") else 1


def _status(args: argparse.Namespace) -> int:
    profile = args.profile
    if args.latest and not profile:
        profile = _latest_profile(
            discover_profiles(extra=args.profile_root), "status"
        )
    if not profile:
        raise SystemExit("status: pass a profile path or --latest")

    try:
        proxy_url = None
        if args.health:
            # Pass the resolved route through even when the port is closed so
            # profile_status can report "skipped" instead of falling back to
            # a direct sweep that only measures timeouts.
            proxy_url = _health_proxy(args.proxy)
        report = profile_status(
            profile,
            state_path=args.state,
            agents_path=defaults.resolve("agents", args.agents),
            agent_names=args.agent or None,
            proxy_url=proxy_url,
            check_agents_run=args.health,
            timeout=args.timeout,
            workers=args.workers,
        )
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise SystemExit(f"status: {exc}") from exc

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    info = report["profile_info"]
    cache = report.get("cache") or {}
    summary = cache.get("summary") or {}
    watch = report.get("watch") or {}
    print(f"profile: {report['profile']}")
    print(f"config:  {report.get('config')}")
    print(
        "groups:  "
        f"{info.get('proxy_groups', 0)} "
        f"(AI: {', '.join(info.get('ai_groups') or []) or 'none'})"
    )
    print(
        "rules:   "
        f"{info.get('rules', 0)}  providers: {info.get('providers', 0)}"
    )
    print(
        "cache:   "
        f"{summary.get('ok', 0)}/{summary.get('managed', 0)} ok, "
        f"{summary.get('missing', 0)} missing, "
        f"{summary.get('mismatch', 0)} mismatch"
    )
    print(
        f"port:    {report.get('port')} "
        f"{'open' if report.get('port_open') else 'closed'}"
    )
    if watch.get("exists"):
        age = watch.get("age_seconds")
        seen = f"seen {age}s ago" if age is not None else "no heartbeat"
        stale = ", STALE" if watch.get("stale") else ""
        print(
            f"watch:   {watch.get('last_action')} at {watch.get('last_run')} "
            f"(ok={watch.get('last_ok')}, {seen}{stale})"
        )
    else:
        print("watch:   no state")
    print(
        "backups: "
        f"profile={report['backups']['profile']} "
        f"rules={report['backups']['rules']}"
    )
    health = report.get("health")
    if health:
        if health.get("skipped"):
            print(f"agents:  skipped ({health['skipped']})")
        elif health.get("error"):
            print(f"agents:  error ({health['error']})")
        else:
            summary = health.get("summary") or {}
            print(
                f"agents:  {summary.get('ok', 0)} ok, "
                f"{summary.get('degraded', 0)} degraded, "
                f"{summary.get('down', 0)} down  via {health.get('proxy') or 'direct'}"
            )
            for item in health.get("agents") or []:
                mark = {"ok": "+", "degraded": "~", "down": "x"}.get(
                    item["status"], "?"
                )
                print(
                    f"  {mark} {item['name']:<14} {item['status']:<9} "
                    f"{item.get('reason') or '-'}"
                )
    print(f"ok:      {report['ok']}")
    for warning in report.get("warnings", []):
        print(f"warning: {warning}")
    for error in report.get("errors", []):
        print(f"error:   {error}")
    return 0 if report["ok"] else 1


def _health(args: argparse.Namespace) -> int:
    try:
        agents = load_agents(defaults.resolve("agents", args.agents))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise SystemExit(f"health: {exc}") from exc

    if args.list:
        for name, agent in agents.items():
            print(
                f"{name:<16} {agent['display_name']:<20} "
                f"service={agent.get('service') or '-':<12} "
                f"checks={len(agent['checks'])}"
            )
        return 0

    names = args.agent or list(agents)
    if args.proxy == "direct":
        proxy_url = None
    elif args.proxy == "auto":
        proxy_url = resolve_proxy_url()
    else:
        proxy_url = args.proxy

    skip = proxy_skip(proxy_url)
    if skip:
        # Never switch routes behind the user's back: a resolved-but-dead proxy
        # means the client is closed, and a direct sweep would report timeouts
        # that say nothing about the agents.
        report = {
            "ok": None,
            "skipped": skip,
            "proxy": proxy_url,
            "proxy_url": proxy_url,
            "proxy_open": False,
            "agents": [],
            "summary": {"total": 0, "ok": 0, "degraded": 0, "down": 0},
        }
        if args.compare:
            # --compare needs a proxied leg; a direct-only sweep would answer a
            # different question and still burn a timeout per endpoint.
            report["compare_skipped"] = True
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"health:  skipped ({skip})")
            print("summary: no checks run; the proxy route is unavailable")
            if args.compare:
                print("compare: skipped; --compare needs a listening proxy")
        return 2

    proxy_state = port_open(proxy_url) if proxy_url else None
    try:
        report = check_agents(
            agents,
            names,
            proxy_url=proxy_url,
            timeout=args.timeout,
            workers=args.workers,
        )
    except KeyError as exc:
        raise SystemExit(f"health: {exc}") from exc

    report["proxy_url"] = proxy_url or "direct"
    report["proxy_open"] = proxy_state
    if args.compare and proxy_url:
        direct = check_agents(
            agents,
            names,
            proxy_url=None,
            timeout=args.timeout,
            workers=args.workers,
        )
        direct_map = {item["name"]: item for item in direct["agents"]}
        for item in report["agents"]:
            direct_item = direct_map[item["name"]]
            item["direct"] = direct_item
            item["diagnosis"] = diagnose(
                item, direct=direct_item, proxy_open=proxy_state
            )
        report["compare"] = True
        report["direct_summary"] = direct["summary"]

    report["ok"] = all(item["status"] != "down" for item in report["agents"])
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    route_label = "direct" if not proxy_url else proxy_url
    if proxy_url:
        print(f"proxy:  {route_label} ({'open' if proxy_state else 'closed'})")
    else:
        print("proxy:  direct")
    if args.compare:
        print(f"{'AGENT':<16} {'PROXY':<8} {'DIRECT':<8} DIAGNOSIS")
        for item in report["agents"]:
            direct_item = item.get("direct") or {}
            diagnosis = item.get("diagnosis") or {}
            print(
                f"{item['name']:<16} {item['status']:<8} "
                f"{direct_item.get('status', '-'):<8} "
                f"{diagnosis.get('code', '-')}"
            )
    else:
        print(f"{'AGENT':<16} {'STATUS':<9} {'ROUTE':<7} {'CHECKS':<7} REASON")
        for item in report["agents"]:
            summary = item["summary"]
            checks = f"{summary['ok']}/{summary['total']}"
            print(
                f"{item['name']:<16} {item['status']:<9} {item['route']:<7} "
                f"{checks:<7} {item.get('reason') or '-'}"
            )
    summary = report["summary"]
    print(
        f"summary: {summary['ok']} ok, {summary['degraded']} degraded, "
        f"{summary['down']} down"
    )
    return 0 if report["ok"] else 1


def _verify(args: argparse.Namespace) -> int:
    args.out = str(paths.resolve_out(args.out))
    coverage_path = os.path.join(args.out, "coverage.json")
    if not os.path.exists(coverage_path):
        raise SystemExit(f"{coverage_path}: not found; run build first")
    with open(coverage_path, encoding="utf-8") as fh:
        coverage = json.load(fh)
    probes = load_probes(os.path.join(args.out, "probes.json"))
    result = verify_outputs(args.out, coverage, probes=probes)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def _ui(args: argparse.Namespace) -> int:
    from .ui import serve

    serve(args.port, open_browser=not args.no_open)
    return 0


def _service(args: argparse.Namespace) -> int:
    action = args.service_command
    if action == "install":
        report = service_backend.install(
            meta=args.meta,
            interval=args.interval,
            agents_interval=args.agents_interval,
            python=args.python,
            dry_run=args.dry_run,
        )
    elif action == "uninstall":
        report = service_backend.uninstall(dry_run=args.dry_run)
    elif action == "status":
        report = service_backend.status()
    else:
        report = service_backend.describe(
            meta=args.meta,
            interval=args.interval,
            agents_interval=args.agents_interval,
            python=args.python,
        )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"{report['action']}: {report['backend']} ({report['platform']})")
        for key in ("message", "unit", "command", "detail"):
            value = report.get(key)
            if value:
                print(f"  {key}: {value}")
        for line in report.get("warnings", []):
            print(f"  warning: {line}")
        for line in report.get("notes", []):
            print(f"  note: {line}")
        for line in report.get("errors", []):
            print(f"  error: {line}")
        tails = report.get("log_tail") or {}
        for stream in ("err", "out"):
            lines = tails.get(stream) or []
            if lines:
                print(f"  last {stream} log lines:")
                for line in lines:
                    print(f"    {line}")

    if not report.get("ok"):
        return 1
    if action == "status":
        return 0 if report.get("installed") and report.get("running") else 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ai-rulec",
        description=(
            "Compile AI-service rule catalogs into mihomo, sing-box and Surge "
            "rules, then apply them to the running client and keep them there."
        ),
    )
    parser.add_argument("--version", action="version", version=f"ai-rulec {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="parse sources, build catalog, emit configs")
    build.add_argument("--services", default=None, help="services catalog (default: bundled)")
    build.add_argument("--sources", default=None, help="source list (default: bundled)")
    build.add_argument("--out", default=None, help="build directory (default: ./out)")
    build.add_argument("--max-age-days", type=int, default=90)
    build.add_argument("--skip-verify", action="store_true")
    build.add_argument(
        "--probe",
        action="store_true",
        help="probe catalog domains over DNS/TLS/HTTP before emitting",
    )
    build.add_argument("--probe-timeout", type=float, default=4.0)
    build.add_argument("--probe-workers", type=int, default=24)
    build.add_argument(
        "--probe-proxy",
        default="auto",
        help="auto (local mihomo if listening), direct, or an explicit proxy URL",
    )
    build.add_argument(
        "--reuse-probes",
        action="store_true",
        help="reuse out/probes.json instead of probing again",
    )
    build.add_argument(
        "--probe-gate",
        choices=("off", "dns", "mismatch", "both"),
        default="both",
        help="strictly exclude dns_fail / tls_mismatch domains (default: both)",
    )
    build.add_argument(
        "--asn-mode",
        choices=("keep", "drop"),
        default="keep",
        help="keep IP-ASN rules or drop them (drop avoids the ASN.mmdb dependency)",
    )
    build.add_argument(
        "--raw",
        action="store_true",
        help="emit shared/unknown/reject-set domains too (default: strict)",
    )
    build.add_argument(
        "--formats",
        nargs="+",
        choices=("mihomo", "sing-box", "surge", "all"),
        default=["mihomo"],
        help="client formats to emit (default: mihomo; 'all' = every format)",
    )
    build.set_defaults(func=_build)

    probe = sub.add_parser("probe", help="probe catalog domains over DNS/TLS/HTTP")
    probe.add_argument("--out", default=None, help="build directory (default: ./out or the last build)")
    probe.add_argument("--timeout", type=float, default=4.0)
    probe.add_argument("--workers", type=int, default=24)
    probe.add_argument("--limit", type=int, default=0)
    probe.add_argument(
        "--proxy",
        default="auto",
        help="auto (local mihomo if listening), direct, or an explicit proxy URL",
    )
    probe.set_defaults(func=_probe)

    merge = sub.add_parser(
        "merge",
        help="merge AI rule-providers and groups into an existing mihomo profile",
    )
    merge.add_argument("--base", required=True, help="existing mihomo/FlClash YAML")
    merge.add_argument("--meta", default=None, help="meta file (default: <out>/mihomo-meta.yaml)")
    merge.add_argument("--out", default="out/merged.yaml")
    merge.add_argument("--target", default=None, help="fallback egress group (default: infer url-test/selector group)")
    merge.add_argument(
        "--map",
        action="append",
        default=[],
        help="service=target override; repeat or comma-separate",
    )
    merge.add_argument(
        "--rules-dir",
        default=None,
        help="directory containing AI-Rules/*.yaml (default: meta file directory)",
    )
    merge.add_argument(
        "--rules-position",
        choices=("before-match", "top"),
        default="before-match",
        help="insert AI rules at the top or just before MATCH",
    )
    merge.add_argument("--mihomo", default=None, help="mihomo binary path")
    merge.set_defaults(func=_merge)

    discover = sub.add_parser(
        "discover", help="find local Clash/mihomo/FlClash profiles"
    )
    discover.add_argument(
        "--root",
        action="append",
        default=[],
        help="extra config root or YAML file to scan",
    )
    discover.add_argument("--json", action="store_true")
    discover.set_defaults(func=_discover)

    apply = sub.add_parser(
        "apply",
        help="merge into a profile in place (backup, copy providers, validate)",
    )
    apply.add_argument("profile", nargs="?", default=None)
    apply.add_argument(
        "--latest",
        action="store_true",
        help=(
            "use the client's selected profile; if unknown, a managed profile, "
            "then the newest file"
        ),
    )
    apply.add_argument(
        "--profile-root",
        action="append",
        default=[],
        help="extra config root for --latest discovery",
    )
    apply.add_argument("--meta", default=None, help="meta file (default: <out>/mihomo-meta.yaml)")
    apply.add_argument("--out", default=None, help="write to another path")
    apply.add_argument("--target", default=None, help="fallback egress group (default: infer url-test/selector group)")
    apply.add_argument(
        "--map",
        action="append",
        default=[],
        help="service=target override; repeat or comma-separate",
    )
    apply.add_argument("--rules-dir", default=None)
    apply.add_argument("--mihomo", default=None)
    apply.add_argument("--no-backup", action="store_true")
    apply.add_argument("--no-copy-rules", action="store_true")
    apply.add_argument(
        "--no-repair-cache",
        action="store_true",
        help="do not copy AI-Rules back into config.yaml provider cache paths",
    )
    apply.add_argument(
        "--reset-proxies",
        action="store_true",
        help="ignore existing AI-<service> choices and use --target",
    )
    apply.add_argument(
        "--rules-last",
        action="store_true",
        help="insert AI rules just before MATCH instead of at the top",
    )
    apply.add_argument("--dry-run", action="store_true")
    apply.add_argument("--json", action="store_true")
    apply.set_defaults(func=_apply)

    surge = sub.add_parser(
        "surge",
        help="patch a Surge profile with AI groups/rules (lists + surge-cli check)",
    )
    surge.add_argument("profile", nargs="?", default=None)
    surge.add_argument("--target", default=None, help="existing Surge policy (default: infer)")
    surge.add_argument(
        "--map",
        action="append",
        default=[],
        help="service=target override; repeat or comma-separate",
    )
    surge.add_argument("--out", default=None, help="build dir holding surge/AI-Rules (default: ./out or the last build)")
    surge.add_argument("--lists-dir", default=None, help="override AI-Rules list source")
    surge.add_argument("--services-file", default=None)
    surge.add_argument("--no-backup", action="store_true")
    surge.add_argument(
        "--rules-last",
        action="store_true",
        help="insert AI rules just before FINAL instead of at the top",
    )
    surge.add_argument("--dry-run", action="store_true")
    surge.add_argument("--json", action="store_true")
    surge.set_defaults(func=_surge)

    singbox = sub.add_parser(
        "singbox",
        help="patch a sing-box/Hiddify config with AI selectors and local rule-sets",
    )
    singbox.add_argument("config", nargs="?", default=None)
    singbox.add_argument("--target", default=None, help="existing outbound tag (default: infer select)")
    singbox.add_argument(
        "--map",
        action="append",
        default=[],
        help="service=outbound override; repeat or comma-separate",
    )
    singbox.add_argument("--out", default=None, help="build dir holding singbox/AI-Rules (default: ./out or the last build)")
    singbox.add_argument("--lists-dir", default=None, help="override rule-set source")
    singbox.add_argument("--rules-dir", default="AI-Rules", help="rule-set dir relative to config")
    singbox.add_argument("--services-file", default=None)
    singbox.add_argument("--absolute-paths", action="store_true")
    singbox.add_argument("--no-backup", action="store_true")
    singbox.add_argument("--dry-run", action="store_true")
    singbox.add_argument("--json", action="store_true")
    singbox.set_defaults(func=_singbox)

    singbox_watch = sub.add_parser(
        "singbox-watch",
        help="re-apply the sing-box/Hiddify patch after subscription rewrites",
    )
    singbox_watch.add_argument("config", nargs="?", default=None)
    singbox_watch.add_argument("--target", default=None)
    singbox_watch.add_argument(
        "--map",
        action="append",
        default=[],
        help="service=outbound override; repeat or comma-separate",
    )
    singbox_watch.add_argument("--out", default=None, help="build dir holding singbox/AI-Rules (default: ./out or the last build)")
    singbox_watch.add_argument("--lists-dir", default=None)
    singbox_watch.add_argument("--rules-dir", default="AI-Rules")
    singbox_watch.add_argument("--services-file", default=None)
    singbox_watch.add_argument("--interval", type=float, default=5.0)
    singbox_watch.add_argument("--once", action="store_true")
    singbox_watch.add_argument("--dry-run", action="store_true")
    singbox_watch.add_argument("--no-backup", action="store_true")
    singbox_watch.add_argument("--force", action="store_true", help="re-apply even if AI groups exist")
    singbox_watch.add_argument("--state", default=None, help="state file (default: next to config)")
    singbox_watch.add_argument("--json", action="store_true")
    singbox_watch.set_defaults(func=_singbox_watch)

    rollback = sub.add_parser(
        "rollback",
        help="restore a profile and AI-Rules from an apply backup",
    )
    rollback.add_argument("profile", nargs="?", default=None)
    rollback.add_argument("--latest", action="store_true")
    rollback.add_argument("--profile-root", action="append", default=[])
    rollback.add_argument("--backup", default=None, help="profile backup path")
    rollback.add_argument("--rules-backup", default=None, help="AI-Rules backup path")
    rollback.add_argument("--no-rules", action="store_true", help="keep current AI-Rules")
    rollback.add_argument("--mihomo", default=None)
    rollback.add_argument("--dry-run", action="store_true")
    rollback.add_argument("--list", action="store_true", help="list available backups")
    rollback.add_argument("--json", action="store_true")
    rollback.set_defaults(func=_rollback)

    doctor = sub.add_parser(
        "doctor", help="check/repair FlClash's hashed AI provider cache"
    )
    doctor.add_argument("profile", nargs="?", default=None)
    doctor.add_argument("--latest", action="store_true")
    doctor.add_argument("--profile-root", action="append", default=[])
    doctor.add_argument("--rules-dir", default=None)
    doctor.add_argument("--fix", action="store_true", help="copy missing/mismatched payloads")
    doctor.add_argument("--dry-run", action="store_true")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=_doctor)

    watch = sub.add_parser(
        "watch",
        help="watch config.yaml; repair cache or reapply the managed profile",
    )
    watch.add_argument("profile", nargs="?", default=None)
    watch.add_argument("--latest", action="store_true")
    watch.add_argument("--profile-root", action="append", default=[])
    watch.add_argument("--meta", default=None, help="meta file (default: <out>/mihomo-meta.yaml)")
    watch.add_argument("--target", default=None, help="fallback egress group (default: infer url-test/selector group)")
    watch.add_argument(
        "--map",
        action="append",
        default=[],
        help="service=target override; repeat or comma-separate",
    )
    watch.add_argument("--rules-dir", default=None)
    watch.add_argument("--mihomo", default=None)
    watch.add_argument("--state", default=None, help="watch state JSON path")
    watch.add_argument("--interval", type=float, default=2.0)
    watch.add_argument(
        "--settle",
        type=float,
        default=0.35,
        help="seconds to wait for config.yaml to stop changing (default: 0.35)",
    )
    watch.add_argument("--once", action="store_true", help="reconcile once and exit")
    watch.add_argument(
        "--fix-cache",
        action="store_true",
        default=True,
        help="repair missing/mismatched provider cache files (default: on)",
    )
    watch.add_argument("--no-fix-cache", action="store_false", dest="fix_cache")
    watch.add_argument(
        "--reapply",
        action="store_true",
        default=True,
        help="re-apply the profile when config.yaml has no AI providers (default: on)",
    )
    watch.add_argument("--no-reapply", action="store_false", dest="reapply")
    watch.add_argument("--dry-run", action="store_true")
    watch.add_argument(
        "--health",
        action="store_true",
        help="also check AI agent endpoints/CLIs on a slower cadence",
    )
    watch.add_argument("--agents", default=None)
    watch.add_argument("--agent", action="append", default=[])
    watch.add_argument("--agents-interval", type=float, default=300.0)
    watch.add_argument("--agents-timeout", type=float, default=6.0)
    watch.add_argument("--json", action="store_true")
    watch.set_defaults(func=_watch)

    status = sub.add_parser(
        "status", help="show profile, cache, port, and watch health"
    )
    status.add_argument("profile", nargs="?", default=None)
    status.add_argument("--latest", action="store_true")
    status.add_argument("--profile-root", action="append", default=[])
    status.add_argument("--state", default=None, help="watch state JSON path")
    status.add_argument(
        "--health",
        action="store_true",
        help="also run AI agent endpoint/CLI checks",
    )
    status.add_argument("--agents", default=None)
    status.add_argument("--agent", action="append", default=[])
    status.add_argument(
        "--proxy",
        default="auto",
        help="auto (local FlClash/mihomo), direct, or an explicit proxy URL",
    )
    status.add_argument("--timeout", type=float, default=6.0)
    status.add_argument("--workers", type=int, default=6)
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=_status)

    health = sub.add_parser(
        "health", help="check AI agent endpoints and local CLIs"
    )
    health.add_argument("--agents", default=None)
    health.add_argument(
        "--proxy",
        default="auto",
        help="auto (local FlClash/mihomo), direct, or an explicit proxy URL",
    )
    health.add_argument(
        "--compare",
        action="store_true",
        help="also check direct and diagnose proxy-vs-upstream failures",
    )
    health.add_argument("--timeout", type=float, default=6.0)
    health.add_argument("--workers", type=int, default=6)
    health.add_argument(
        "--agent",
        action="append",
        default=[],
        help="only check the named agent; repeatable",
    )
    health.add_argument("--list", action="store_true", help="list configured agents")
    health.add_argument("--json", action="store_true")
    health.set_defaults(func=_health)

    verify = sub.add_parser("verify", help="validate emitted configs and providers")
    verify.add_argument("--out", default=None, help="build directory (default: ./out or the last build)")
    verify.set_defaults(func=_verify)

    ui = sub.add_parser(
        "ui", help="open the local web console (loopback + token)"
    )
    ui.add_argument("--port", type=int, default=8787)
    ui.add_argument("--no-open", action="store_true", help="do not launch a browser")
    ui.set_defaults(func=_ui)

    service = sub.add_parser(
        "service",
        help="install/status/uninstall ai-rulec watch as a per-user service",
    )
    service_sub = service.add_subparsers(dest="service_command", required=True)
    for name, help_text in (
        ("install", "write the unit/task and start it"),
        ("status", "show whether the service is installed and running"),
        ("uninstall", "stop and remove the unit/task"),
        ("describe", "show the backend, unit path and command line (writes nothing)"),
    ):
        action = service_sub.add_parser(name, help=help_text)
        if name in ("install", "describe"):
            action.add_argument(
                "--meta",
                default=None,
                help="meta file (default: the last build, or ./out/mihomo-meta.yaml)",
            )
            action.add_argument(
                "--interval", type=float, default=service_backend.DEFAULT_INTERVAL
            )
            action.add_argument(
                "--agents-interval",
                type=float,
                default=service_backend.DEFAULT_AGENTS_INTERVAL,
            )
            action.add_argument(
                "--python",
                default=None,
                help="interpreter for the service (default: the current one)",
            )
        if name in ("install", "uninstall"):
            action.add_argument(
                "--dry-run", action="store_true", help="render only, write nothing"
            )
        action.add_argument("--json", action="store_true")
        action.set_defaults(func=_service)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
