#!/usr/bin/env python3
"""CLI entrypoint for ruminate: discover, extract, and inspect local/export AI chat histories."""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from adapters import ADAPTER_MAP, ALL_ADAPTERS  # noqa: E402
from adapters.base import (  # noqa: E402
    Session,
    SourceReport,
    format_iso,
    open_sqlite_readonly,
    paginate_sessions,
    parse_timestamp,
)


def resolve_window(days: int, since_str: Optional[str], until_str: Optional[str]) -> Tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    until = parse_timestamp(until_str) if until_str else now
    if until is None:
        raise ValueError(f"Invalid --until date: {until_str}")
    if until_str and len(until_str.strip()) == 10:
        until = until.replace(hour=23, minute=59, second=59)
    since = parse_timestamp(since_str) if since_str else (until - timedelta(days=days))
    if since is None:
        raise ValueError(f"Invalid --since date: {since_str}")
    return since, until


def select_adapters(sources_arg: Optional[str], has_extra_paths: bool) -> List[Any]:
    if sources_arg:
        names = [s.strip() for s in sources_arg.split(",") if s.strip()]
        unknown = [n for n in names if n not in ADAPTER_MAP]
        if unknown:
            raise ValueError(f"Unknown source(s): {', '.join(unknown)}")
        return [ADAPTER_MAP[n] for n in names]
    if has_extra_paths:
        return list(ALL_ADAPTERS)
    return [a for a in ALL_ADAPTERS if a.tier in ("local", "hybrid")]


def cmd_discover(args: argparse.Namespace) -> int:
    since, until = resolve_window(args.days, args.since, args.until)
    home_dir = Path(args.home).expanduser() if args.home else None
    extra_paths = [Path(p).expanduser() for p in (args.path or [])]
    adapters = select_adapters(args.sources, bool(extra_paths))

    reports: List[SourceReport] = []
    for adapter in adapters:
        if adapter.tier in ("export", "generic") and not extra_paths:
            continue
        try:
            reports.append(adapter.discover(since, until, home_dir=home_dir, extra_paths=extra_paths))
        except PermissionError as exc:
            reports.append(
                SourceReport(
                    adapter.name,
                    adapter.display_name,
                    adapter.tier,
                    "permission_denied",
                    note=str(exc),
                )
            )

    payload = {
        "window": {"since": format_iso(since), "until": format_iso(until)},
        "sources": [r.to_dict() for r in reports],
    }
    if args.format == "table":
        print(f"Window: {payload['window']['since']} -> {payload['window']['until']}")
        print(f"{'SOURCE':<16} {'TIER':<8} {'STATUS':<24} {'SESSIONS':>8} {'BYTES':>10}")
        for r in reports:
            print(
                f"{r.source:<16} {r.tier:<8} {r.status:<24} {r.sessions_in_window:>8} {r.approx_bytes:>10}"
            )
            if r.note:
                print(f"  note: {r.note}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    since, until = resolve_window(args.days, args.since, args.until)
    home_dir = Path(args.home).expanduser() if args.home else None
    extra_paths = [Path(p).expanduser() for p in (args.path or [])]
    adapters = select_adapters(args.sources, bool(extra_paths))

    sessions: List[Session] = []
    for adapter in adapters:
        if adapter.tier in ("export", "generic") and not extra_paths:
            continue
        sessions.extend(adapter.extract(since, until, home_dir=home_dir, extra_paths=extra_paths))

    paginated = paginate_sessions(
        sessions,
        max_chars=args.max_chars,
        per_session_chars=args.per_session_chars,
        chunk=args.chunk,
    )
    paginated["window"] = {"since": format_iso(since), "until": format_iso(until)}

    if args.format == "markdown":
        print(
            f"# Extracted Transcripts (Chunk {paginated['chunk']}/{paginated['total_chunks']}, "
            f"{paginated['sessions_in_chunk']}/{paginated['total_sessions']} sessions)\n"
        )
        for s in paginated["sessions"]:
            url_part = f" | {s['url']}" if s.get("url") else ""
            print(f"## [{s['app']} | {s['updated_at'][:10]} | {s['title']}]{url_part}")
            for t in s["turns"]:
                print(f"**{t['role']}**: {t['text']}\n")
    else:
        print(json.dumps(paginated, ensure_ascii=False, indent=2))
    return 0


def _inspect_file(path: Path) -> Dict[str, Any]:
    suf = path.suffix.lower()
    if suf in (".sqlite", ".sqlite3", ".db", ".vscdb"):
        try:
            conn = open_sqlite_readonly(path)
        except sqlite3.DatabaseError as exc:
            return {"path": str(path), "type": "sqlite", "status": "encrypted_or_unreadable", "error": str(exc)}
        try:
            tables: Dict[str, List[str]] = {}
            for (tbl,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{tbl}")').fetchall()]
                tables[tbl] = cols
            return {"path": str(path), "type": "sqlite", "status": "ok", "tables": tables}
        finally:
            conn.close()

    if suf == ".jsonl":
        keys = set()
        lines_checked = 0
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        keys.update(obj.keys())
                except json.JSONDecodeError:
                    pass
                lines_checked += 1
                if lines_checked >= 20:
                    break
        return {"path": str(path), "type": "jsonl", "sample_keys": sorted(keys)}

    if suf == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            return {"path": str(path), "type": "json", "error": str(exc)}
        if isinstance(data, dict):
            return {"path": str(path), "type": "json_object", "top_keys": sorted(data.keys())}
        if isinstance(data, list):
            sample_keys = sorted(data[0].keys()) if data and isinstance(data[0], dict) else []
            return {"path": str(path), "type": "json_array", "length": len(data), "item_keys": sample_keys}
        return {"path": str(path), "type": type(data).__name__}

    return {"path": str(path), "type": suf or "unknown"}


def cmd_inspect(args: argparse.Namespace) -> int:
    target = Path(args.path).expanduser()
    if not target.exists():
        print(json.dumps({"error": f"Path not found: {target}"}))
        return 1
    results: List[Dict[str, Any]] = []
    if target.is_file():
        results.append(_inspect_file(target))
    else:
        for child in sorted(target.rglob("*")):
            if child.is_file() and child.suffix.lower() in (".jsonl", ".json", ".sqlite", ".sqlite3", ".db", ".vscdb"):
                results.append(_inspect_file(child))
                if len(results) >= 30:
                    break
    print(json.dumps({"inspected": results}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discover and extract AI chat histories for ruminate.")
    sub = parser.add_subparsers(dest="command", required=True)

    for cmd_name in ("discover", "extract"):
        p = sub.add_parser(cmd_name)
        p.add_argument("--days", type=int, default=7, help="Lookback window in days (default: 7)")
        p.add_argument("--since", help="Start date/timestamp (ISO 8601 or YYYY-MM-DD)")
        p.add_argument("--until", help="End date/timestamp (ISO 8601 or YYYY-MM-DD)")
        p.add_argument("--sources", help="Comma-separated source IDs")
        p.add_argument("--path", action="append", help="Export file/zip or Markdown directory (repeatable)")
        p.add_argument("--home", help="Override home directory (for testing)")
        if cmd_name == "discover":
            p.add_argument("--format", choices=("json", "table"), default="json")
        else:
            p.add_argument("--max-chars", type=int, default=80000)
            p.add_argument("--per-session-chars", type=int, default=4000)
            p.add_argument("--chunk", type=int, default=1)
            p.add_argument("--format", choices=("json", "markdown"), default="json")

    insp = sub.add_parser("inspect", help="Safely inspect local DB/JSON/JSONL structure without dumping secrets.")
    insp.add_argument("--path", required=True, help="File or directory to inspect")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "discover":
        return cmd_discover(args)
    if args.command == "extract":
        return cmd_extract(args)
    if args.command == "inspect":
        return cmd_inspect(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
