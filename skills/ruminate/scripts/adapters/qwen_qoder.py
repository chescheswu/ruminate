"""Qwen Code CLI and Qoder local session adapter."""

from datetime import datetime
import json
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


class QwenQoderAdapter(BaseAdapter):
    name = "qwen-qoder"
    display_name = "Qwen Code / Qoder"
    tier = "local"

    def _roots(self, home_dir: Optional[Path]) -> List[Path]:
        home = home_dir or Path.home()
        return [
            home / ".qwen" / "history",
            home / ".qwen" / "projects",
            home / ".qoder" / "projects",
        ]

    def _find_files(self, home_dir: Optional[Path]) -> List[Path]:
        readable, _ = probe_roots(self._roots(home_dir))
        files: List[Path] = []
        for root in readable:
            files.extend(p for p in root.rglob("*.jsonl") if p.is_file())
        return sorted(files)

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
            files = self._find_files(home_dir)
            in_win = [f for f in files if in_window(file_mtime_utc(f), since, until)]
            total_bytes = sum(f.stat().st_size for f in in_win)
            status = "available" if in_win else ("permission_denied" if blocked else "empty")
            note = f"Blocked: {', '.join(blocked)}" if blocked else ""
            return SourceReport(
                self.name, self.display_name, self.tier, status, len(in_win), total_bytes, existing + blocked, note=note
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
        sessions: List[Session] = []
        for f in self._find_files(home_dir):
            try:
                mtime = file_mtime_utc(f)
                if not in_window(mtime, since, until):
                    continue
                turns, latest_dt, title = parse_generic_jsonl(f)
                state_file = f.parent / f.stem / "state.json"
                if state_file.is_file():
                    try:
                        state = json.loads(state_file.read_text(encoding="utf-8", errors="replace"))
                        if isinstance(state, dict) and state.get("title"):
                            title = str(state["title"])
                    except (OSError, json.JSONDecodeError):
                        pass
            except (OSError, PermissionError):
                continue
            dt = latest_dt or mtime
            if not in_window(dt, since, until) or not turns:
                continue
            app_label = "Qoder" if ".qoder" in f.parts else "Qwen Code"
            sessions.append(
                Session(
                    app=app_label,
                    session_id=f.stem,
                    title=title or derive_title(turns, f.stem),
                    updated_at=format_iso(dt),
                    turns=turns,
                    source_path=str(f),
                )
            )
        return sessions
