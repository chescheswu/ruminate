"""Generic Markdown / plain-text notes directory adapter."""

from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

from .base import (
    BaseAdapter,
    Session,
    SourceReport,
    Turn,
    clean_text,
    file_mtime_utc,
    format_iso,
    in_window,
)


class MarkdownDirAdapter(BaseAdapter):
    name = "markdown-dir"
    display_name = "Markdown Notes"
    tier = "generic"

    def _find_files(self, extra_paths: Optional[Sequence[Path]]) -> List[Path]:
        files: List[Path] = []
        for p in extra_paths or []:
            if p.is_file() and p.suffix.lower() in (".md", ".txt"):
                files.append(p)
            elif p.is_dir():
                for child in p.rglob("*"):
                    if child.is_file() and child.suffix.lower() in (".md", ".txt"):
                        files.append(child)
        return sorted(set(files))

    def discover(
        self,
        since: datetime,
        until: datetime,
        home_dir: Optional[Path] = None,
        extra_paths: Optional[Sequence[Path]] = None,
    ) -> SourceReport:
        if not extra_paths:
            return SourceReport(self.name, self.display_name, self.tier, "not_found")
        files = self._find_files(extra_paths)
        in_win = [f for f in files if in_window(file_mtime_utc(f), since, until)]
        if not in_win:
            return SourceReport(self.name, self.display_name, self.tier, "empty", paths=[str(p) for p in extra_paths])
        return SourceReport(
            self.name,
            self.display_name,
            self.tier,
            "available",
            len(in_win),
            sum(f.stat().st_size for f in in_win),
            [str(p) for p in extra_paths],
        )

    def extract(
        self,
        since: datetime,
        until: datetime,
        home_dir: Optional[Path] = None,
        extra_paths: Optional[Sequence[Path]] = None,
    ) -> List[Session]:
        sessions: List[Session] = []
        for f in self._find_files(extra_paths):
            try:
                mtime = file_mtime_utc(f)
                if not in_window(mtime, since, until):
                    continue
                raw = f.read_text(encoding="utf-8", errors="replace")
            except (OSError, PermissionError):
                continue
            cleaned = clean_text(raw)
            if not cleaned:
                continue
            first_heading = next(
                (ln.lstrip("# ").strip() for ln in cleaned.splitlines() if ln.strip().startswith("#")),
                f.stem,
            )
            sessions.append(
                Session(
                    app=self.display_name,
                    session_id=f.stem,
                    title=first_heading or f.stem,
                    updated_at=format_iso(mtime),
                    turns=[Turn(role="user", text=cleaned, timestamp=format_iso(mtime))],
                    source_path=str(f),
                )
            )
        return sessions
