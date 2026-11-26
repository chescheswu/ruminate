"""Official cloud export adapters: Google Takeout (Gemini Apps), ChatGPT, and Claude."""

from datetime import datetime
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple
import zipfile

from .base import (
    BaseAdapter,
    Session,
    SourceReport,
    Turn,
    derive_title,
    extract_text_from_content,
    format_iso,
    in_window,
    normalize_turns,
    parse_timestamp,
)


def _iter_candidate_files(extra_paths: Optional[Sequence[Path]]) -> List[Path]:
    found: List[Path] = []
    for p in extra_paths or []:
        if p.is_file():
            found.append(p)
        elif p.is_dir():
            for child in p.rglob("*"):
                if child.is_file() and child.suffix.lower() in (".json", ".zip", ".html"):
                    found.append(child)
    return sorted(set(found))


def _read_json_from_file_or_zip(
    path: Path, member_predicate
) -> List[Tuple[str, Any]]:
    results: List[Tuple[str, Any]] = []
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            results.append((str(path), data))
        except (OSError, json.JSONDecodeError):
            pass
    elif path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(path, "r") as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    if member_predicate(info.filename):
                        raw = zf.read(info.filename).decode("utf-8", errors="replace")
                        try:
                            results.append((f"{path}!{info.filename}", json.loads(raw)))
                        except json.JSONDecodeError:
                            continue
        except (OSError, zipfile.BadZipFile):
            pass
    return results


# --- Gemini Takeout ---


_GEMINI_URL_ID_RE = re.compile(r"/app/c/([A-Za-z0-9_-]+)")


class _TakeoutHTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.blocks: List[str] = []
        self._capture = False
        self._buf: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("div", "p", "li"):
            self._capture = True
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("div", "p", "li") and self._capture:
            text = "".join(self._buf).strip()
            if text:
                self.blocks.append(text)
            self._capture = False

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buf.append(data)


class GeminiTakeoutAdapter(BaseAdapter):
    name = "gemini-takeout"
    display_name = "Gemini (Takeout)"
    tier = "export"

    @staticmethod
    def _is_gemini_activity_member(member_name: str) -> bool:
        low = member_name.lower()
        return low.endswith("myactivity.json") and "gemini" in low

    @staticmethod
    def _is_gemini_activity_list(data: Any) -> bool:
        if not isinstance(data, list) or not data:
            return False
        first = data[0]
        return isinstance(first, dict) and (
            "Gemini Apps" in first.get("products", [])
            or "gemini" in str(first.get("header", "")).lower()
        )

    def _collect_records(
        self, extra_paths: Optional[Sequence[Path]]
    ) -> Tuple[List[Tuple[str, List[Dict[str, Any]]]], List[Tuple[str, str]]]:
        json_sources: List[Tuple[str, List[Dict[str, Any]]]] = []
        html_sources: List[Tuple[str, str]] = []
        for p in _iter_candidate_files(extra_paths):
            if p.suffix.lower() == ".html" and "myactivity" in p.name.lower():
                try:
                    html_sources.append((str(p), p.read_text(encoding="utf-8", errors="replace")))
                except OSError:
                    pass
                continue
            if p.suffix.lower() == ".json":
                for src, data in _read_json_from_file_or_zip(p, self._is_gemini_activity_member):
                    if self._is_gemini_activity_list(data):
                        json_sources.append((src, data))
            elif p.suffix.lower() == ".zip":
                for src, data in _read_json_from_file_or_zip(p, self._is_gemini_activity_member):
                    if self._is_gemini_activity_list(data):
                        json_sources.append((src, data))
                try:
                    with zipfile.ZipFile(p, "r") as zf:
                        for info in zf.infolist():
                            low = info.filename.lower()
                            if low.endswith("myactivity.html") and "gemini" in low:
                                html_sources.append(
                                    (
                                        f"{p}!{info.filename}",
                                        zf.read(info.filename).decode("utf-8", errors="replace"),
                                    )
                                )
                except (OSError, zipfile.BadZipFile):
                    pass
        return json_sources, html_sources

    def discover(
        self,
        since: datetime,
        until: datetime,
        home_dir: Optional[Path] = None,
        extra_paths: Optional[Sequence[Path]] = None,
    ) -> SourceReport:
        if not extra_paths:
            return SourceReport(self.name, self.display_name, self.tier, "not_found")
        sessions = self.extract(since, until, home_dir=home_dir, extra_paths=extra_paths)
        if not sessions:
            return SourceReport(self.name, self.display_name, self.tier, "not_found")
        paths = sorted({s.source_path for s in sessions})
        approx_bytes = sum(sum(len(t.text) for t in s.turns) for s in sessions)
        return SourceReport(
            self.name, self.display_name, self.tier, "available", len(sessions), approx_bytes, paths
        )

    def extract(
        self,
        since: datetime,
        until: datetime,
        home_dir: Optional[Path] = None,
        extra_paths: Optional[Sequence[Path]] = None,
    ) -> List[Session]:
        json_sources, html_sources = self._collect_records(extra_paths)
        sessions: List[Session] = []

        for src, records in json_sources:
            grouped: Dict[str, List[Tuple[datetime, str, str, str]]] = {}
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                dt = parse_timestamp(rec.get("time"))
                if not in_window(dt, since, until) or dt is None:
                    continue
                url = str(rec.get("titleUrl") or "")
                m = _GEMINI_URL_ID_RE.search(url)
                conv_id = m.group(1) if m else f"day-{dt.strftime('%Y-%m-%d')}"
                title_raw = str(rec.get("title") or "").strip()
                prompt_text = re.sub(r"^Prompted[:\s]+", "", title_raw, flags=re.IGNORECASE).strip()
                details_parts = []
                for d in rec.get("details") or []:
                    if isinstance(d, dict) and d.get("value"):
                        details_parts.append(str(d["value"]))
                resp_text = "\n".join(details_parts).strip()
                grouped.setdefault(conv_id, []).append((dt, prompt_text, resp_text, url))

            for conv_id, events in grouped.items():
                events.sort(key=lambda x: x[0])
                raw_turns: List[Tuple[str, str, Optional[str]]] = []
                conv_url = ""
                for dt, prompt_text, resp_text, url in events:
                    ts = format_iso(dt)
                    if url and not conv_url:
                        conv_url = url
                    if prompt_text:
                        raw_turns.append(("user", prompt_text, ts))
                    if resp_text:
                        raw_turns.append(("assistant", resp_text, ts))
                turns = normalize_turns(raw_turns)
                if not turns:
                    continue
                latest_dt = events[-1][0]
                sessions.append(
                    Session(
                        app=self.display_name,
                        session_id=conv_id,
                        title=derive_title(turns, conv_id),
                        updated_at=format_iso(latest_dt),
                        turns=turns,
                        source_path=src,
                        url=conv_url,
                    )
                )

        if not sessions and html_sources:
            for src, html in html_sources:
                parser = _TakeoutHTMLTextExtractor()
                parser.feed(html)
                prompts = [
                    re.sub(r"^Prompted[:\s]+", "", b, flags=re.IGNORECASE).strip()
                    for b in parser.blocks
                    if b.lower().startswith("prompted")
                ]
                turns = normalize_turns([("user", p, None) for p in prompts if p])
                if turns:
                    sessions.append(
                        Session(
                            app=self.display_name,
                            session_id="takeout-html",
                            title="Gemini Takeout HTML Activity (switch Takeout to JSON for full threads)",
                            updated_at=format_iso(until),
                            turns=turns,
                            source_path=src,
                        )
                    )
        return sessions


# --- ChatGPT Export ---


class ChatGPTExportAdapter(BaseAdapter):
    name = "chatgpt-export"
    display_name = "ChatGPT (Export)"
    tier = "export"

    @staticmethod
    def _is_chatgpt_list(data: Any) -> bool:
        return (
            isinstance(data, list)
            and bool(data)
            and isinstance(data[0], dict)
            and "mapping" in data[0]
        )

    def discover(
        self,
        since: datetime,
        until: datetime,
        home_dir: Optional[Path] = None,
        extra_paths: Optional[Sequence[Path]] = None,
    ) -> SourceReport:
        if not extra_paths:
            return SourceReport(self.name, self.display_name, self.tier, "not_found")
        sessions = self.extract(since, until, home_dir=home_dir, extra_paths=extra_paths)
        if not sessions:
            return SourceReport(self.name, self.display_name, self.tier, "not_found")
        return SourceReport(
            self.name,
            self.display_name,
            self.tier,
            "available",
            len(sessions),
            sum(sum(len(t.text) for t in s.turns) for s in sessions),
            sorted({s.source_path for s in sessions}),
        )

    def extract(
        self,
        since: datetime,
        until: datetime,
        home_dir: Optional[Path] = None,
        extra_paths: Optional[Sequence[Path]] = None,
    ) -> List[Session]:
        sessions: List[Session] = []
        for p in _iter_candidate_files(extra_paths):
            for src, data in _read_json_from_file_or_zip(
                p, lambda name: name.lower().endswith("conversations.json")
            ):
                if not self._is_chatgpt_list(data):
                    continue
                for conv in data:
                    if not isinstance(conv, dict):
                        continue
                    dt = parse_timestamp(conv.get("update_time") or conv.get("create_time"))
                    if not in_window(dt, since, until) or dt is None:
                        continue
                    mapping = conv.get("mapping") or {}
                    nodes: List[Tuple[float, str, str, Optional[str]]] = []
                    for node in mapping.values():
                        if not isinstance(node, dict):
                            continue
                        msg = node.get("message")
                        if not isinstance(msg, dict):
                            continue
                        author = (msg.get("author") or {}).get("role")
                        content = msg.get("content") or {}
                        text = extract_text_from_content(content.get("parts") or content.get("text") or content)
                        msg_dt = parse_timestamp(msg.get("create_time"))
                        order = float(msg.get("create_time") or 0.0)
                        if author and text:
                            nodes.append((order, str(author), text, format_iso(msg_dt) if msg_dt else None))
                    nodes.sort(key=lambda x: x[0])
                    turns = normalize_turns([(r, t, ts) for _, r, t, ts in nodes])
                    if not turns:
                        continue
                    sid = str(conv.get("conversation_id") or conv.get("id") or "chatgpt")
                    title = str(conv.get("title") or "") or derive_title(turns, sid)
                    sessions.append(
                        Session(
                            app=self.display_name,
                            session_id=sid,
                            title=title,
                            updated_at=format_iso(dt),
                            turns=turns,
                            source_path=src,
                        )
                    )
        return sessions


# --- Claude Export ---


class ClaudeExportAdapter(BaseAdapter):
    name = "claude-export"
    display_name = "Claude (Export)"
    tier = "export"

    @staticmethod
    def _is_claude_list(data: Any) -> bool:
        return (
            isinstance(data, list)
            and bool(data)
            and isinstance(data[0], dict)
            and "chat_messages" in data[0]
        )

    def discover(
        self,
        since: datetime,
        until: datetime,
        home_dir: Optional[Path] = None,
        extra_paths: Optional[Sequence[Path]] = None,
    ) -> SourceReport:
        if not extra_paths:
            return SourceReport(self.name, self.display_name, self.tier, "not_found")
        sessions = self.extract(since, until, home_dir=home_dir, extra_paths=extra_paths)
        if not sessions:
            return SourceReport(self.name, self.display_name, self.tier, "not_found")
        return SourceReport(
            self.name,
            self.display_name,
            self.tier,
            "available",
            len(sessions),
            sum(sum(len(t.text) for t in s.turns) for s in sessions),
            sorted({s.source_path for s in sessions}),
        )

    def extract(
        self,
        since: datetime,
        until: datetime,
        home_dir: Optional[Path] = None,
        extra_paths: Optional[Sequence[Path]] = None,
    ) -> List[Session]:
        sessions: List[Session] = []
        for p in _iter_candidate_files(extra_paths):
            for src, data in _read_json_from_file_or_zip(
                p, lambda name: name.lower().endswith("conversations.json")
            ):
                if not self._is_claude_list(data):
                    continue
                for conv in data:
                    if not isinstance(conv, dict):
                        continue
                    dt = parse_timestamp(conv.get("updated_at") or conv.get("created_at"))
                    if not in_window(dt, since, until) or dt is None:
                        continue
                    raw_turns: List[Tuple[str, str, Optional[str]]] = []
                    for msg in conv.get("chat_messages") or []:
                        if not isinstance(msg, dict):
                            continue
                        role = msg.get("sender") or msg.get("role")
                        text = extract_text_from_content(msg.get("text") or msg.get("content"))
                        msg_dt = parse_timestamp(msg.get("created_at"))
                        if role and text:
                            raw_turns.append((str(role), text, format_iso(msg_dt) if msg_dt else None))
                    turns = normalize_turns(raw_turns)
                    if not turns:
                        continue
                    sid = str(conv.get("uuid") or conv.get("id") or "claude")
                    title = str(conv.get("name") or conv.get("title") or "") or derive_title(turns, sid)
                    sessions.append(
                        Session(
                            app=self.display_name,
                            session_id=sid,
                            title=title,
                            updated_at=format_iso(dt),
                            turns=turns,
                            source_path=src,
                        )
                    )
        return sessions
