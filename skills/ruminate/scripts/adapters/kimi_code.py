"""Kimi Code CLI & legacy kimi-cli local session adapter."""

from datetime import datetime
import json
import os
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


class KimiCodeAdapter(BaseAdapter):
    name = "kimi-code"
    display_name = "Kimi Code"
    tier = "local"

    def _roots(self, home_dir: Optional[Path]) -> List[Path]:
        roots: List[Path] = []
        if home_dir is None:
            if os.environ.get("KIMI_CODE_HOME"):
                roots.append(Path(os.environ["KIMI_CODE_HOME"]) / "sessions")
            if os.environ.get("KIMI_SHARE_DIR"):
                roots.append(Path(os.environ["KIMI_SHARE_DIR"]) / "sessions")
        home = home_dir or Path.home()
        for candidate in (home / ".kimi-code" / "sessions", home / ".kimi" / "sessions"):
            if candidate not in roots:
                roots.append(candidate)
        return roots

    def _find_files(self, home_dir: Optional[Path]) -> List[Path]:
        readable, _ = probe_roots(self._roots(home_dir))
        files: List[Path] = []
        seen_dirs = set()
        for root in readable:
            for name in ("context.jsonl", "wire.jsonl"):
                for p in root.rglob(name):
                    if p.is_file() and p.parent not in seen_dirs:
                        # Prefer context.jsonl over wire.jsonl when both exist in same session dir
                        if name == "wire.jsonl" and (p.parent / "context.jsonl").is_file():
                            continue
                        seen_dirs.add(p.parent)
                        files.append(p)
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
                # Check sibling state.json for custom title
                for state_candidate in (f.parent / "state.json", f.parents[2] / "state.json"):
                    if state_candidate.is_file():
                        try:
                            state = json.loads(state_candidate.read_text(encoding="utf-8", errors="replace"))
                            if isinstance(state, dict) and state.get("title"):
                                title = str(state["title"])
                                break
                        except (OSError, json.JSONDecodeError):
                            pass
            except (OSError, PermissionError):
                continue
            dt = latest_dt or mtime
            if not in_window(dt, since, until) or not turns:
                continue
            sid = f.parent.name if f.parent.name != "main" else f.parents[2].name
            sessions.append(
                Session(
                    app=self.display_name,
                    session_id=sid,
                    title=title or derive_title(turns, sid),
                    updated_at=format_iso(dt),
                    turns=turns,
                    source_path=str(f),
                )
            )
        return sessions
