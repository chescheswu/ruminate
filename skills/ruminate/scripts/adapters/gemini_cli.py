"""Gemini CLI local session adapter."""

from datetime import datetime
import json
import os
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

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
    parse_generic_jsonl,
    parse_timestamp,
    probe_roots,
)


class GeminiCliAdapter(BaseAdapter):
    name = "gemini-cli"
    display_name = "Gemini CLI"
    tier = "local"

    def _roots(self, home_dir: Optional[Path]) -> List[Path]:
        env_dir = os.environ.get("GEMINI_DATA_DIR")
        if env_dir and home_dir is None:
            return [Path(env_dir)]
        home = home_dir or Path.home()
        return [
            home / ".gemini" / "tmp",
            home / ".config" / "gemini-cli" / "sessions",
        ]

    def _find_files(self, home_dir: Optional[Path]) -> List[Path]:
        readable, _ = probe_roots(self._roots(home_dir))
        files: List[Path] = []
        for root in readable:
            if root.name == "tmp":
                for pattern in ("*/chats/*.json", "*/chats/*.jsonl"):
                    files.extend(p for p in root.glob(pattern) if p.is_file())
            else:
                for pattern in ("*.json", "*.jsonl"):
                    files.extend(p for p in root.glob(pattern) if p.is_file())
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

    def _parse_json_chat(self, path: Path) -> Tuple[List[Session], bool]:
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            return [], False
        if not isinstance(data, dict) or "messages" not in data:
            return [], False
        raw_turns: List[Tuple[str, str, Optional[str]]] = []
        latest_dt = parse_timestamp(data.get("lastUpdated") or data.get("updatedAt") or data.get("startTime"))
        for msg in data.get("messages", []):
            if not isinstance(msg, dict):
                continue
            role = msg.get("type") or msg.get("role") or msg.get("author")
            if str(role).lower() == "gemini":
                role = "assistant"
            text = extract_text_from_content(msg.get("content") or msg.get("text") or msg.get("parts"))
            dt = parse_timestamp(msg.get("timestamp"))
            if dt and (latest_dt is None or dt > latest_dt):
                latest_dt = dt
            if role and text:
                raw_turns.append((str(role), text, format_iso(dt) if dt else None))
        turns = normalize_turns(raw_turns)
        if not turns:
            return [], True
        dt = latest_dt or file_mtime_utc(path)
        sid = str(data.get("sessionId") or path.stem)
        title = str(data.get("title") or data.get("summary") or "") or derive_title(turns, sid)
        return [
            Session(
                app=self.display_name,
                session_id=sid,
                title=title,
                updated_at=format_iso(dt),
                turns=turns,
                source_path=str(path),
            )
        ], True

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
                if f.suffix.lower() == ".json":
                    parsed, handled = self._parse_json_chat(f)
                    if handled:
                        for s in parsed:
                            dt = parse_timestamp(s.updated_at)
                            if in_window(dt, since, until):
                                sessions.append(s)
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
        return sessions
