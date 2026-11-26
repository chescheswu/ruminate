"""Shared models, normalization, secret redaction, SQLite/JSONL helpers, and pagination."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote


@dataclass
class Turn:
    role: str  # "user" or "assistant"
    text: str
    timestamp: Optional[str] = None


@dataclass
class Session:
    app: str
    session_id: str
    title: str
    updated_at: str
    turns: List[Turn] = field(default_factory=list)
    source_path: str = ""
    url: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        signals = self.metadata.get("pre_compaction_signals") or compute_session_signals(self.turns)
        return {
            "app": self.app,
            "session_id": self.session_id,
            "title": self.title,
            "updated_at": self.updated_at,
            "url": self.url,
            "source_path": self.source_path,
            "signals": signals,
            "turns": [
                {
                    "role": t.role,
                    "text": t.text,
                    **({"timestamp": t.timestamp} if t.timestamp else {}),
                }
                for t in self.turns
            ],
        }


@dataclass
class SourceReport:
    source: str
    display_name: str
    tier: str
    status: str  # available | not_found | empty | permission_denied | encrypted_or_unreadable
    sessions_in_window: int = 0
    approx_bytes: int = 0
    paths: List[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "display_name": self.display_name,
            "tier": self.tier,
            "status": self.status,
            "sessions_in_window": self.sessions_in_window,
            "approx_bytes": self.approx_bytes,
            "paths": self.paths,
            "note": self.note,
        }


class BaseAdapter:
    name: str = ""
    display_name: str = ""
    tier: str = "local"  # local | hybrid | export | generic

    def discover(
        self,
        since: datetime,
        until: datetime,
        home_dir: Optional[Path] = None,
        extra_paths: Optional[Sequence[Path]] = None,
    ) -> SourceReport:
        raise NotImplementedError

    def extract(
        self,
        since: datetime,
        until: datetime,
        home_dir: Optional[Path] = None,
        extra_paths: Optional[Sequence[Path]] = None,
    ) -> List[Session]:
        raise NotImplementedError


# --- Time helpers ---


def parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse ISO 8601 strings or epoch seconds/milliseconds into UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:  # milliseconds
            ts /= 1000.0
        if ts <= 0:
            return None
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.isdigit():
            return parse_timestamp(int(raw))
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S"):
            try:
                return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def file_mtime_utc(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def in_window(dt: Optional[datetime], since: datetime, until: datetime) -> bool:
    if dt is None:
        return False
    return since <= dt <= until


def format_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --- Secret redaction & noise stripping ---

_SECRET_PATTERNS = [
    # Private key blocks
    (
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
    # Bearer tokens
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]{12,}=*"), "Bearer [REDACTED]"),
    # Anthropic / OpenAI / GitHub / Google / AWS / Slack keys
    (re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{16,}\b"), "[REDACTED]"),
    (re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9\-_]{20,}\b"), "[REDACTED]"),
    (re.compile(r"\bAIza[0-9A-Za-z\-_]{20,}\b"), "[REDACTED]"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[0-9A-Za-z_]{20,}\b"), "[REDACTED]"),
    (re.compile(r"\bgithub_pat_[0-9A-Za-z_]{20,}\b"), "[REDACTED]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED]"),
    (re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,}\b"), "[REDACTED]"),
    # Env-style secret assignments
    (
        re.compile(
            r"(?im)\b([A-Z0-9_]*(?:API_KEY|SECRET|TOKEN|PASSWORD|PASSWD)[A-Z0-9_]*)\s*([:=])\s*(['\"]?)([A-Za-z0-9_\-./+=]{8,})\3"
        ),
        r"\1\2\3[REDACTED]\3",
    ),
]

_SYSTEM_TAG_RE = re.compile(
    r"<(?:system-reminder|SYSTEM_MESSAGE|system_instruction|context|ADDITIONAL_METADATA|USER_SETTINGS_CHANGE)>.*?</(?:system-reminder|SYSTEM_MESSAGE|system_instruction|context|ADDITIONAL_METADATA|USER_SETTINGS_CHANGE)>",
    re.DOTALL | re.IGNORECASE,
)
_USER_REQUEST_TAG_RE = re.compile(r"</?USER_REQUEST>", re.IGNORECASE)
_FENCED_CODE_RE = re.compile(r"```([^\n]*)\n(.*?)```", re.DOTALL)


def redact_secrets(text: str) -> str:
    if not text:
        return ""
    result = text
    for pattern, replacement in _SECRET_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def clean_text(text: str, max_code_chars: int = 600) -> str:
    """Strip system tags, collapse huge code/diff dumps, redact secrets, normalize spacing."""
    if not text:
        return ""
    cleaned = _SYSTEM_TAG_RE.sub("", text)
    cleaned = _USER_REQUEST_TAG_RE.sub("", cleaned)

    def _collapse_code(match: re.Match) -> str:
        lang = match.group(1).strip()
        body = match.group(2)
        if len(body) <= max_code_chars:
            return match.group(0)
        label = f"{lang} " if lang else ""
        return f"[{label}code/output omitted, {len(body)} chars]"

    cleaned = _FENCED_CODE_RE.sub(_collapse_code, cleaned)
    cleaned = redact_secrets(cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def extract_text_from_content(content: Any) -> str:
    """Extract human/assistant text from string or structured block lists, dropping tool noise."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        ctype = str(content.get("type", "")).lower()
        if ctype in {
            "tool_use",
            "tool_call",
            "tool_result",
            "function_call",
            "function_response",
            "thinking",
            "reasoning",
            "image",
            "image_url",
            "audio",
        }:
            return ""
        for key in ("text", "content", "value", "message"):
            if key in content and content[key]:
                return extract_text_from_content(content[key])
        if "parts" in content:
            return extract_text_from_content(content["parts"])
        return ""
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            extracted = extract_text_from_content(item)
            if extracted:
                parts.append(extracted)
        return "\n".join(parts)
    return str(content)


def normalize_role(raw_role: Any) -> Optional[str]:
    if not raw_role:
        return None
    r = str(raw_role).strip().lower()
    if r in {"user", "human", "prompt", "user_input"}:
        return "user"
    if r in {"assistant", "model", "ai", "bot", "gpt", "claude", "gemini", "planner_response"}:
        return "assistant"
    return None


def probe_roots(roots: Sequence[Path]) -> Tuple[List[Path], List[str]]:
    """Probe root directories safely without crashing or swallowing PermissionError."""
    readable: List[Path] = []
    blocked: List[str] = []
    for root in roots:
        try:
            if not root.exists():
                continue
            if root.is_dir():
                os.listdir(root)
            readable.append(root)
        except PermissionError:
            blocked.append(str(root))
    return readable, blocked


def normalize_turns(raw_turns: Iterable[Tuple[str, str, Optional[str]]]) -> List[Turn]:
    turns: List[Turn] = []
    for role, text, ts in raw_turns:
        norm_role = normalize_role(role)
        if not norm_role:
            continue
        cleaned = clean_text(text)
        if not cleaned:
            continue
        turns.append(Turn(role=norm_role, text=cleaned, timestamp=ts))
    return turns


def derive_title(turns: Sequence[Turn], fallback: str) -> str:
    for turn in turns:
        if turn.role == "user" and turn.text:
            first_line = turn.text.splitlines()[0].strip()
            return (first_line[:77] + "...") if len(first_line) > 80 else first_line
    return fallback


# --- Generic JSONL parser ---


def parse_generic_jsonl(
    path: Path,
) -> Tuple[List[Turn], Optional[datetime], Optional[str]]:
    """Parse common JSONL conversation log shapes into normalized turns, latest timestamp, and title."""
    raw_turns: List[Tuple[str, str, Optional[str]]] = []
    latest_dt: Optional[datetime] = None
    found_title: Optional[str] = None

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue

            if not found_title:
                for tk in ("title", "sessionTitle", "summary", "name"):
                    tv = obj.get(tk)
                    if isinstance(tv, str) and tv.strip():
                        found_title = tv.strip()
                        break

            ts_val = (
                obj.get("timestamp")
                or obj.get("createTime")
                or obj.get("created_at")
                or obj.get("time")
                or obj.get("ts")
            )
            dt = parse_timestamp(ts_val)
            if dt and (latest_dt is None or dt > latest_dt):
                latest_dt = dt
            ts_str = format_iso(dt) if dt else None

            # Shape 1: {prompt, response}
            if "prompt" in obj or "response" in obj:
                p = extract_text_from_content(obj.get("prompt"))
                r = extract_text_from_content(obj.get("response"))
                if p:
                    raw_turns.append(("user", p, ts_str))
                if r:
                    raw_turns.append(("assistant", r, ts_str))
                continue

            # Shape 2: Codex {type: response_item|event_msg, payload: {...}}
            if "payload" in obj and isinstance(obj["payload"], dict):
                payload = obj["payload"]
                role = payload.get("role") or payload.get("type")
                text = extract_text_from_content(
                    payload.get("content") or payload.get("message") or payload.get("text")
                )
                if role and text:
                    raw_turns.append((str(role), text, ts_str))
                continue

            # Shape 3: Claude Code / CodeBuddy / WorkBuddy {type: user|assistant, message: {role, content}}
            if "message" in obj and isinstance(obj["message"], dict):
                msg = obj["message"]
                role = msg.get("role") or obj.get("type")
                text = extract_text_from_content(
                    msg.get("content") or msg.get("text") or msg.get("parts")
                )
                if role and text:
                    raw_turns.append((str(role), text, ts_str))
                continue

            # Shape 4: Flat {role|author|sender|type, content|text|parts|message}
            role = (
                obj.get("role")
                or obj.get("author")
                or obj.get("sender")
                or obj.get("type")
            )
            content = (
                obj.get("content")
                if "content" in obj
                else obj.get("text")
                if "text" in obj
                else obj.get("parts")
                if "parts" in obj
                else obj.get("message")
            )
            text = extract_text_from_content(content)
            if role and text:
                raw_turns.append((str(role), text, ts_str))

    return normalize_turns(raw_turns), latest_dt, found_title


# --- Safe read-only SQLite helpers ---


def open_sqlite_readonly(path: Path) -> sqlite3.Connection:
    """Open an SQLite file strictly read-only without modifying WAL/journal state."""
    encoded = quote(str(path.resolve()))
    try:
        conn = sqlite3.connect(f"file:{encoded}?mode=ro&immutable=1", uri=True)
        conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        return conn
    except sqlite3.DatabaseError:
        conn = sqlite3.connect(f"file:{encoded}?mode=ro", uri=True)
        conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        return conn


def extract_generic_sqlite_sessions(
    path: Path,
    app_name: str,
    since: datetime,
    until: datetime,
) -> List[Session]:
    """Schema-flexible read-only extractor for desktop SQLite session/message stores."""
    conn = open_sqlite_readonly(path)
    sessions: List[Session] = []
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        session_meta: Dict[str, Tuple[str, Optional[datetime]]] = {}
        for tbl in tables:
            tl = tbl.lower()
            if not any(k in tl for k in ("session", "conversation", "chat", "thread")):
                continue
            cols = [
                r[1] for r in conn.execute(f'PRAGMA table_info("{tbl}")').fetchall()
            ]
            id_col = next((c for c in cols if c.lower() in ("id", "session_id", "conversation_id", "uuid")), None)
            title_col = next((c for c in cols if c.lower() in ("title", "name", "summary", "topic")), None)
            time_col = next((c for c in cols if c.lower() in ("updated_at", "created_at", "timestamp", "time", "update_time")), None)
            if not id_col:
                continue
            select_cols = [id_col] + ([title_col] if title_col else []) + ([time_col] if time_col else [])
            q = f'SELECT {", ".join(f"{c}" for c in select_cols)} FROM "{tbl}"'
            for row in conn.execute(q).fetchall():
                sid = str(row[0])
                idx = 1
                title = str(row[idx]) if title_col and row[idx] else sid
                if title_col:
                    idx += 1
                dt = parse_timestamp(row[idx]) if time_col else None
                session_meta[sid] = (title, dt)

        grouped: Dict[str, List[Tuple[str, str, Optional[str], Optional[datetime]]]] = {}
        for tbl in tables:
            tl = tbl.lower()
            if not any(k in tl for k in ("message", "turn", "record", "history", "chat_item")):
                continue
            cols = [
                r[1] for r in conn.execute(f'PRAGMA table_info("{tbl}")').fetchall()
            ]
            role_col = next((c for c in cols if c.lower() in ("role", "sender", "author", "type")), None)
            text_col = next((c for c in cols if c.lower() in ("content", "text", "message", "body", "prompt")), None)
            if not role_col or not text_col:
                continue
            sid_col = next((c for c in cols if c.lower() in ("session_id", "conversation_id", "chat_id", "thread_id")), None)
            time_col = next((c for c in cols if c.lower() in ("created_at", "timestamp", "time", "updated_at")), None)
            sel = [role_col, text_col] + ([sid_col] if sid_col else []) + ([time_col] if time_col else [])
            q = f'SELECT {", ".join(f"{c}" for c in sel)} FROM "{tbl}"'
            for row in conn.execute(q).fetchall():
                role = str(row[0] or "")
                raw_content = row[1]
                if isinstance(raw_content, (bytes, bytearray)):
                    raw_content = raw_content.decode("utf-8", errors="replace")
                text = extract_text_from_content(raw_content)
                idx = 2
                sid = str(row[idx]) if sid_col and row[idx] is not None else path.stem
                if sid_col:
                    idx += 1
                dt = parse_timestamp(row[idx]) if time_col else None
                ts_str = format_iso(dt) if dt else None
                grouped.setdefault(sid, []).append((role, text, ts_str, dt))

        mtime = file_mtime_utc(path)
        for sid, items in grouped.items():
            title, meta_dt = session_meta.get(sid, (sid, None))
            dts = [x[3] for x in items if x[3] is not None]
            effective_dt = max(dts) if dts else (meta_dt or mtime)
            if not in_window(effective_dt, since, until):
                continue
            turns = normalize_turns([(r, t, ts) for r, t, ts, _ in items])
            if not turns:
                continue
            sessions.append(
                Session(
                    app=app_name,
                    session_id=sid,
                    title=title if title != sid else derive_title(turns, sid),
                    updated_at=format_iso(effective_dt),
                    turns=turns,
                    source_path=str(path),
                )
            )
    finally:
        conn.close()
    return sessions


# --- Compaction & pagination ---


_OPEN_LOOP_RE = re.compile(
    r"(?:\b(?:later|revisit|next time)\b|以后|有空|回头|先记|能不能|如果)",
    re.IGNORECASE,
)
_CLOSING_RE = re.compile(
    r"(?:\b(?:thanks|thank you|got it|done|fixed|solved|works now)\b|搞定|谢谢|感谢|已解决|没问题了)",
    re.IGNORECASE,
)


def compute_session_signals(turns: Sequence[Turn]) -> Dict[str, Any]:
    """Compute lightweight structural hints without mutating transcript content."""
    turn_count = len(turns)
    user_turns = [t for t in turns if t.role == "user"]
    total_chars = sum(len(t.text) for t in turns)
    user_chars = sum(len(t.text) for t in user_turns)
    user_char_ratio = round(user_chars / total_chars, 2) if total_chars > 0 else 0.0

    ends_with_open_question = False
    if user_turns:
        last_user = user_turns[-1].text.strip()
        if not _CLOSING_RE.search(last_user):
            if last_user.endswith(("?", "？")) or _OPEN_LOOP_RE.search(last_user):
                ends_with_open_question = True

    return {
        "turn_count": turn_count,
        "user_turn_count": len(user_turns),
        "user_char_ratio": user_char_ratio,
        "ends_with_open_question": ends_with_open_question,
    }


def compact_session(session: Session, per_session_chars: int = 4000) -> Session:
    """Keep first user prompt + latest turns within per_session_chars budget."""
    if per_session_chars <= 0 or not session.turns:
        return session
    pre_signals = compute_session_signals(session.turns)
    total = sum(len(t.text) + 16 for t in session.turns)
    if total <= per_session_chars:
        return session

    kept: List[Turn] = []
    budget = per_session_chars
    first_turn = session.turns[0]
    first_text = first_turn.text[: max(200, budget // 3)]
    kept.append(Turn(role=first_turn.role, text=first_text, timestamp=first_turn.timestamp))
    budget -= len(first_text) + 16

    tail: List[Turn] = []
    for turn in reversed(session.turns[1:]):
        cost = len(turn.text) + 16
        if cost <= budget:
            tail.append(turn)
            budget -= cost
        elif budget > 160 and not tail:
            tail.append(
                Turn(
                    role=turn.role,
                    text=turn.text[-(budget - 20) :],
                    timestamp=turn.timestamp,
                )
            )
            budget = 0
            break
        else:
            break
    tail.reverse()
    if len(kept) + len(tail) < len(session.turns):
        kept.append(
            Turn(
                role="assistant",
                text=f"[... {len(session.turns) - len(kept) - len(tail)} middle turns omitted for budget ...]",
            )
        )
    kept.extend(tail)
    meta = dict(session.metadata)
    meta["pre_compaction_signals"] = pre_signals
    return Session(
        app=session.app,
        session_id=session.session_id,
        title=session.title,
        updated_at=session.updated_at,
        turns=kept,
        source_path=session.source_path,
        url=session.url,
        metadata=meta,
    )


def paginate_sessions(
    sessions: List[Session],
    max_chars: int = 80000,
    per_session_chars: int = 4000,
    chunk: int = 1,
) -> Dict[str, Any]:
    """Sort newest-first, compact each session, and split deterministically into chunks."""
    ordered = sorted(sessions, key=lambda s: s.updated_at, reverse=True)
    compacted = [compact_session(s, per_session_chars=per_session_chars) for s in ordered]

    chunks: List[List[Dict[str, Any]]] = []
    current_batch: List[Dict[str, Any]] = []
    current_chars = 0

    for sess in compacted:
        payload = sess.to_dict()
        size = len(json.dumps(payload, ensure_ascii=False))
        if current_batch and current_chars + size > max_chars:
            chunks.append(current_batch)
            current_batch = []
            current_chars = 0
        current_batch.append(payload)
        current_chars += size

    if current_batch or not chunks:
        chunks.append(current_batch)

    total_chunks = len(chunks)
    safe_chunk = max(1, min(chunk, total_chunks))
    selected = chunks[safe_chunk - 1]
    return {
        "chunk": safe_chunk,
        "total_chunks": total_chunks,
        "total_sessions": len(compacted),
        "sessions_in_chunk": len(selected),
        "char_count": sum(len(json.dumps(s, ensure_ascii=False)) for s in selected),
        "sessions": selected,
    }


DEFAULT_SKIP_DIRS = frozenset(
    {
        "tool-results",
        "subagents",
        "blobs",
        "blob_storage",
        "cache",
        "caches",
        "code cache",
        "gpucache",
        "dawncache",
        "crashpad",
        "logs",
        "log",
        "tmp",
        "temp",
        "plugins",
        "skills",
        "node_modules",
        "extensions",
        "local storage",
        "session storage",
        "indexeddb",
        "telemetry",
        "traces",
        "metrics",
        "backups",
        "archive",
        ".git",
        "webstorage",
        "databases",
    }
)

NOISY_JSONL_NAMES = frozenset(
    {
        "telemetry.jsonl",
        "trace.jsonl",
        "traces.jsonl",
        "events.jsonl",
        "metrics.jsonl",
        "debug.jsonl",
    }
)


def iter_filtered_files(
    root: Path,
    suffixes: Sequence[str],
    max_depth: Optional[int] = None,
    skip_dirs: Optional[Sequence[str]] = None,
) -> List[Path]:
    """Walk root pruning noisy cache/tool/subagent directories and optionally limiting depth."""
    if not root.exists():
        return []
    skip_set = frozenset(d.lower() for d in (skip_dirs if skip_dirs is not None else DEFAULT_SKIP_DIRS))
    suffix_set = frozenset(s.lower() for s in suffixes)
    results: List[Path] = []
    root_depth = len(root.parts)

    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _err: None):
        current = Path(dirpath)
        depth = len(current.parts) - root_depth
        if max_depth is not None and depth >= max_depth:
            dirnames[:] = []
        else:
            dirnames[:] = [d for d in dirnames if d.lower() not in skip_set]
        for fname in filenames:
            low_name = fname.lower()
            if low_name in NOISY_JSONL_NAMES:
                continue
            if any(low_name.endswith(suf) for suf in suffix_set):
                results.append(current / fname)
    return sorted(results)
