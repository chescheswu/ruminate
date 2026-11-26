"""WorkBuddy hybrid local + cloud workspace adapter (JSONL + read-only SQLite)."""

from datetime import datetime
from pathlib import Path
import sqlite3
from typing import List, Optional, Sequence, Tuple

from .base import (
    BaseAdapter,
    Session,
    SourceReport,
    derive_title,
    extract_generic_sqlite_sessions,
    file_mtime_utc,
    format_iso,
    in_window,
    iter_filtered_files,
    open_sqlite_readonly,
    parse_generic_jsonl,
    probe_roots,
)


class WorkBuddyAdapter(BaseAdapter):
    name = "workbuddy"
    display_name = "WorkBuddy"
    tier = "hybrid"

    def _roots(self, home_dir: Optional[Path]) -> List[Path]:
        home = home_dir or Path.home()
        return [home / ".workbuddy", home / ".workbuddy-ai"]

    def _find_files(self, home_dir: Optional[Path]) -> Tuple[List[Path], List[Path]]:
        readable, _ = probe_roots(self._roots(home_dir))
        jsonls: List[Path] = []
        dbs: List[Path] = []
        for root in readable:
            found_for_root: List[Path] = []
            for sub_name in ("projects", "sessions"):
                sub = root / sub_name
                if sub.is_dir():
                    found_for_root.extend(iter_filtered_files(sub, suffixes=(".jsonl",)))
            if not found_for_root:
                hist = root / "history.jsonl"
                if hist.is_file():
                    found_for_root.append(hist)
            jsonls.extend(found_for_root)
            dbs.extend(iter_filtered_files(root, suffixes=(".sqlite", ".sqlite3", ".db"), max_depth=2))
        return sorted(set(jsonls)), sorted(set(dbs))

    def discover(
        self,
        since: datetime,
        until: datetime,
        home_dir: Optional[Path] = None,
        extra_paths: Optional[Sequence[Path]] = None,
    ) -> SourceReport:
        readable, blocked = probe_roots(self._roots(home_dir))
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
            readable_dbs: List[Path] = []
            encrypted_dbs = 0
            for db in dbs:
                if not in_window(file_mtime_utc(db), since, until):
                    continue
                try:
                    conn = open_sqlite_readonly(db)
                    conn.close()
                    readable_dbs.append(db)
                except sqlite3.DatabaseError:
                    encrypted_dbs += 1
            count = len(in_win_jsonls) + len(readable_dbs)
            total_bytes = sum(f.stat().st_size for f in (in_win_jsonls + readable_dbs))
            if count == 0 and encrypted_dbs > 0:
                return SourceReport(
                    self.name,
                    self.display_name,
                    self.tier,
                    "encrypted_or_unreadable",
                    0,
                    0,
                    existing + blocked,
                    note="Detected encrypted or unreadable SQLite database(s); export chat history or use Markdown fallback.",
                )
            status = "available" if count else ("permission_denied" if blocked else "empty")
            notes = []
            if encrypted_dbs:
                notes.append(f"{encrypted_dbs} encrypted DB(s) skipped")
            if blocked:
                notes.append(f"Blocked: {', '.join(blocked)}")
            return SourceReport(
                self.name,
                self.display_name,
                self.tier,
                status,
                count,
                total_bytes,
                existing + blocked,
                note="; ".join(notes),
            )
        except PermissionError as exc:
            return SourceReport(
                self.name, self.display_name, self.tier, "permission_denied", paths=existing + blocked, note=str(exc)
            )

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
            try:
                if not in_window(file_mtime_utc(db), since, until):
                    continue
                sessions.extend(extract_generic_sqlite_sessions(db, self.display_name, since, until))
            except (sqlite3.DatabaseError, OSError, PermissionError):
                continue
        return sessions
