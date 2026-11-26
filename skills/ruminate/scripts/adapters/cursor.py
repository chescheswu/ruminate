"""Cursor IDE & Agent transcripts adapter (JSONL + read-only state.vscdb SQLite)."""

from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .base import (
    BaseAdapter,
    Session,
    SourceReport,
    derive_title,
    extract_text_from_content,
    file_mtime_utc,
    format_iso,
    in_window,
    normalize_turns,
    open_sqlite_readonly,
    parse_generic_jsonl,
    parse_timestamp,
    probe_roots,
)


class CursorAdapter(BaseAdapter):
    name = "cursor"
    display_name = "Cursor"
    tier = "local"

    def _candidate_roots(self, home_dir: Optional[Path]) -> Tuple[List[Path], List[Path]]:
        home = home_dir or Path.home()
        jsonl_roots = [home / ".cursor" / "projects"]
        vscdb_roots = [
            home / "Library" / "Application Support" / "Cursor" / "User",
            home / ".config" / "Cursor" / "User",
            home / "AppData" / "Roaming" / "Cursor" / "User",
        ]
        return jsonl_roots, vscdb_roots

    def _find_files(self, home_dir: Optional[Path]) -> Tuple[List[Path], List[Path]]:
        jsonl_roots, vscdb_roots = self._candidate_roots(home_dir)
        readable_jsonl, _ = probe_roots(jsonl_roots)
        readable_vscdb, _ = probe_roots(vscdb_roots)
        jsonls: List[Path] = []
        dbs: List[Path] = []
        for root in readable_jsonl:
            jsonls.extend(p for p in root.glob("*/agent-transcripts/*.jsonl") if p.is_file())
        for root in readable_vscdb:
            global_db = root / "globalStorage" / "state.vscdb"
            if global_db.is_file():
                dbs.append(global_db)
            ws_dir = root / "workspaceStorage"
            if ws_dir.exists():
                dbs.extend(p for p in ws_dir.glob("*/state.vscdb") if p.is_file())
        return sorted(jsonls), sorted(dbs)

    def discover(
        self,
        since: datetime,
        until: datetime,
        home_dir: Optional[Path] = None,
        extra_paths: Optional[Sequence[Path]] = None,
    ) -> SourceReport:
        jsonl_roots, vscdb_roots = self._candidate_roots(home_dir)
        readable, blocked = probe_roots(jsonl_roots + vscdb_roots)
        if not readable and blocked:
            return SourceReport(
                self.name,
                self.display_name,
                self.tier,
                "permission_denied",
                paths=blocked,
                note=f"Permission denied accessing: {', '.join(blocked)}",
            )
        if not readable:
            return SourceReport(self.name, self.display_name, self.tier, "not_found")
        existing = [str(r) for r in readable]
        try:
            jsonls, dbs = self._find_files(home_dir)
            in_win_jsonls = [f for f in jsonls if in_window(file_mtime_utc(f), since, until)]
            in_win_dbs = [d for d in dbs if in_window(file_mtime_utc(d), since, until)]
            count = len(in_win_jsonls) + len(in_win_dbs)
            total_bytes = sum(f.stat().st_size for f in (in_win_jsonls + in_win_dbs))
            status = "available" if count else ("permission_denied" if blocked else "empty")
            note = f"Blocked: {', '.join(blocked)}" if blocked else ""
            return SourceReport(
                self.name,
                self.display_name,
                self.tier,
                status,
                count,
                total_bytes,
                existing + blocked,
                note=note,
            )
        except PermissionError as exc:
            return SourceReport(
                self.name, self.display_name, self.tier, "permission_denied", paths=existing + blocked, note=str(exc)
            )

    def _extract_vscdb(self, db_path: Path, since: datetime, until: datetime) -> List[Session]:
        sessions: List[Session] = []
        mtime = file_mtime_utc(db_path)
        if not in_window(mtime, since, until):
            return sessions
        try:
            conn = open_sqlite_readonly(db_path)
        except sqlite3.DatabaseError:
            return sessions
        try:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            for tbl in ("cursorDiskKV", "ItemTable"):
                if tbl not in tables:
                    continue
                rows = conn.execute(
                    f'SELECT key, value FROM "{tbl}" WHERE key LIKE "%composerData%" OR key LIKE "%aichat%"'
                ).fetchall()
                for key, val in rows:
                    if isinstance(val, (bytes, bytearray)):
                        val = val.decode("utf-8", errors="replace")
                    if not isinstance(val, str):
                        continue
                    try:
                        data = json.loads(val)
                    except json.JSONDecodeError:
                        continue
                    sessions.extend(self._parse_vscdb_blob(str(key), data, db_path, mtime, since, until))
        finally:
            conn.close()
        return sessions

    def _parse_vscdb_blob(
        self,
        key: str,
        data: Any,
        db_path: Path,
        mtime: datetime,
        since: datetime,
        until: datetime,
    ) -> List[Session]:
        results: List[Session] = []
        if not isinstance(data, dict):
            return results
        items = data.get("tabs") or data.get("allComposers") or [data]
        if not isinstance(items, list):
            return results
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            raw_msgs = item.get("bubbles") or item.get("conversation") or item.get("messages") or []
            if not isinstance(raw_msgs, list) or not raw_msgs:
                continue
            dt = parse_timestamp(item.get("lastUpdatedAt") or item.get("createdAt")) or mtime
            if not in_window(dt, since, until):
                continue
            raw_turns: List[Tuple[str, str, Optional[str]]] = []
            for m in raw_msgs:
                if not isinstance(m, dict):
                    continue
                role = m.get("type") or m.get("role")
                if role == 1:
                    role = "user"
                elif role == 2:
                    role = "assistant"
                text = extract_text_from_content(m.get("text") or m.get("content") or m.get("rawText"))
                ts_dt = parse_timestamp(m.get("timestamp") or m.get("createdAt"))
                if role and text:
                    raw_turns.append((str(role), text, format_iso(ts_dt) if ts_dt else None))
            turns = normalize_turns(raw_turns)
            if not turns:
                continue
            sid = str(item.get("composerId") or item.get("tabId") or f"{key}-{idx}")
            title = str(item.get("name") or item.get("chatTitle") or "") or derive_title(turns, sid)
            results.append(
                Session(
                    app=self.display_name,
                    session_id=sid,
                    title=title,
                    updated_at=format_iso(dt),
                    turns=turns,
                    source_path=str(db_path),
                )
            )
        return results

    def extract(
        self,
        since: datetime,
        until: datetime,
        home_dir: Optional[Path] = None,
        extra_paths: Optional[Sequence[Path]] = None,
    ) -> List[Session]:
        jsonls, dbs = self._find_files(home_dir)
        sessions: List[Session] = []
        for f in jsonls:
            try:
                mtime = file_mtime_utc(f)
                if not in_window(mtime, since, until):
                    continue
                turns, latest_dt, title = parse_generic_jsonl(f)
            except (OSError, PermissionError):
                continue
            dt = latest_dt or mtime
            if in_window(dt, since, until) and turns:
                sessions.append(
                    Session(
                        app=self.display_name,
                        session_id=f.stem,
                        title=title or derive_title(turns, f.stem),
                        updated_at=format_iso(dt),
                        turns=turns,
                        source_path=str(f),
                    )
                )
        for db in dbs:
            sessions.extend(self._extract_vscdb(db, since, until))
        return sessions
