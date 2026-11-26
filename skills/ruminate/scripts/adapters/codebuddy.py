"""CodeBuddy local session adapter."""

from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

from .base import (
    BaseAdapter,
    Session,
    SourceReport,
    derive_title,
    file_mtime_utc,
    format_iso,
    in_window,
    parse_generic_jsonl,
    probe_roots,
)


class CodeBuddyAdapter(BaseAdapter):
    name = "codebuddy"
    display_name = "CodeBuddy"
    tier = "local"

    def _base_dir(self, home_dir: Optional[Path]) -> Path:
        return (home_dir or Path.home()) / ".codebuddy"

    def _find_files(self, home_dir: Optional[Path]) -> List[Path]:
        readable, _ = probe_roots([self._base_dir(home_dir)])
        files: List[Path] = []
        for base in readable:
            projects = base / "projects"
            if projects.exists():
                files.extend(p for p in projects.rglob("*.jsonl") if p.is_file() and "tool-results" not in p.parts)
            hist = base / "history.jsonl"
            if hist.is_file() and not files:
                files.append(hist)
        return sorted(files)

    def discover(
        self,
        since: datetime,
        until: datetime,
        home_dir: Optional[Path] = None,
        extra_paths: Optional[Sequence[Path]] = None,
    ) -> SourceReport:
        readable, blocked = probe_roots([self._base_dir(home_dir)])
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
            files = self._find_files(home_dir)
            in_win = [f for f in files if in_window(file_mtime_utc(f), since, until)]
            total_bytes = sum(f.stat().st_size for f in in_win)
            return SourceReport(
                self.name,
                self.display_name,
                self.tier,
                "available" if in_win else "empty",
                len(in_win),
                total_bytes,
                existing,
            )
        except PermissionError as exc:
            return SourceReport(
                self.name, self.display_name, self.tier, "permission_denied", paths=existing, note=str(exc)
            )

    def extract(
        self,
        since: datetime,
        until: datetime,
        home_dir: Optional[Path] = None,
        extra_paths: Optional[Sequence[Path]] = None,
    ) -> List[Session]:
        sessions: List[Session] = []
        for f in self._find_files(home_dir):
            try:
                mtime = file_mtime_utc(f)
                if not in_window(mtime, since, until):
                    continue
                turns, latest_dt, title = parse_generic_jsonl(f)
            except (OSError, PermissionError):
                continue
            dt = latest_dt or mtime
            if not in_window(dt, since, until) or not turns:
                continue
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
        return sessions
