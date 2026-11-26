"""Unit and end-to-end tests for the ruminate skill (synthetic fixtures only)."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skills" / "ruminate"
SCRIPTS_DIR = SKILL_DIR / "scripts"
HISTORY_TOOL = SCRIPTS_DIR / "history_tool.py"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from adapters import ADAPTER_MAP  # noqa: E402
from adapters.base import clean_text, paginate_sessions, redact_secrets, Session, Turn  # noqa: E402


def _iso(days_ago: float = 0.0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_jsonl(path: Path, rows: list, mtime_days_ago: float = 0.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    if mtime_days_ago:
        ts = (datetime.now(timezone.utc) - timedelta(days=mtime_days_ago)).timestamp()
        os.utime(path, (ts, ts))


def _create_generic_sqlite(db_path: Path, app_tag: str, ts_iso: str) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, updated_at TEXT)")
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, created_at TEXT)"
    )
    conn.execute("INSERT INTO sessions VALUES (?, ?, ?)", ("s-sql-1", f"{app_tag} Idea Session", ts_iso))
    conn.execute(
        "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        ("s-sql-1", "user", f"What if we build a {app_tag} spark collector?", ts_iso),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        ("s-sql-1", "assistant", "That is a promising direction worth prototyping.", ts_iso),
    )
    conn.commit()
    conn.close()


class TestAgentSkillsSpec(unittest.TestCase):
    def test_skill_md_frontmatter_and_portability(self) -> None:
        skill_md = SKILL_DIR / "SKILL.md"
        self.assertTrue(skill_md.is_file())
        content = skill_md.read_text(encoding="utf-8")
        lines = content.splitlines()
        self.assertLess(len(lines), 220)
        self.assertTrue(content.startswith("---\n"))
        parts = content.split("---\n", 2)
        self.assertEqual(len(parts), 3)
        fm = parts[1]

        name_m = re.search(r"^name:\s*(.+)$", fm, re.MULTILINE)
        self.assertIsNotNone(name_m)
        name = name_m.group(1).strip()
        self.assertEqual(name, "ruminate")
        self.assertEqual(name, SKILL_DIR.name)
        self.assertLessEqual(len(name), 64)
        self.assertRegex(name, r"^[a-z0-9]+(-[a-z0-9]+)*$")

        self.assertIn("description:", fm)
        # Ensure all referenced files exist
        for rel in ("references/cloud-sources.md", "references/output-template.md", "references/adding-adapters.md"):
            self.assertIn(rel, content)
            self.assertTrue((SKILL_DIR / rel).is_file())

        # Ensure agent-agnostic wording (no Antigravity-only tool names)
        for forbidden in ("ego-browser", "run_command", "write_file", "vault_log"):
            self.assertNotIn(forbidden, content)
        self.assertIn("Do not read `scripts/` source code", content)

        # Ensure output-template.md contains all 7 signals, 3 exclusions, and Layout C sections
        tpl = (SKILL_DIR / "references" / "output-template.md").read_text(encoding="utf-8")
        for required in (
            "Decision Shift",
            "Model Update",
            "Sharp Knowledge Gap",
            "High-Leverage Inference",
            "Cross-Domain Bridge",
            "Serendipitous Seed",
            "Open Loop & Latent Pattern",
            "Long Debugging Marathons",
            "Delegated One-Off Chores",
            "Pure Lookups",
            "Thinking Radar",
            "Memory Cue Cards",
            "Your exact prompt",
            "Edge Index",
        ):
            self.assertIn(required, tpl)


class TestRedactionNoiseAndPagination(unittest.TestCase):
    def test_secret_redaction(self) -> None:
        openai_key = "sk-proj-" + "abcdefghijklmnopqrstuvwxyz123456"
        anthropic_key = "sk-ant-" + "api03-abcdefghijklmnopqrstuvwxyz123456"
        google_key = "AIza" + "SyDummyKey1234567890abcdefghijk"
        github_key = "ghp_" + "abcdefghijklmnopqrstuvwxyz1234567890"
        aws_key = "AKIA" + "IOSFODNN7EXAMPLE"
        jwt_prefix = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        pem_begin = "-----BEGIN " + "RSA PRIVATE KEY-----"
        pem_end = "-----END " + "RSA PRIVATE KEY-----"
        sample = (
            f"OPENAI_API_KEY={openai_key}\n"
            f"anthropic: {anthropic_key}\n"
            f"google: {google_key}\n"
            f"github: {github_key}\n"
            f"aws: {aws_key}\n"
            f"Authorization: Bearer {jwt_prefix}.test\n"
            f"{pem_begin}\nMIIEowIBAAKCAQEA\n{pem_end}\n"
        )
        redacted = redact_secrets(sample)
        for leak in (
            openai_key,
            anthropic_key,
            google_key,
            github_key,
            aws_key,
            jwt_prefix,
            "MIIEowIBAAKCAQEA",
        ):
            self.assertNotIn(leak, redacted)
        self.assertIn("[REDACTED]", redacted)
        self.assertIn("[REDACTED_PRIVATE_KEY]", redacted)

    def test_noise_stripping(self) -> None:
        huge_block = "```python\n" + ("x = 1\n" * 200) + "```"
        raw = "<system-reminder>hidden</system-reminder>\nHello idea!\n" + huge_block
        cleaned = clean_text(raw, max_code_chars=200)
        self.assertNotIn("hidden", cleaned)
        self.assertIn("Hello idea!", cleaned)
        self.assertIn("code/output omitted", cleaned)

    def test_pagination_preserves_all_sessions(self) -> None:
        sessions = [
            Session(
                app="TestApp",
                session_id=f"id-{i}",
                title=f"Title {i}",
                updated_at=_iso(days_ago=i * 0.1),
                turns=[Turn(role="user", text="Idea " + ("a" * 300))],
            )
            for i in range(5)
        ]
        p1 = paginate_sessions(sessions, max_chars=900, per_session_chars=1000, chunk=1)
        p2 = paginate_sessions(sessions, max_chars=900, per_session_chars=1000, chunk=2)
        self.assertGreater(p1["total_chunks"], 1)
        seen_ids = set()
        for c in range(1, p1["total_chunks"] + 1):
            res = paginate_sessions(sessions, max_chars=900, per_session_chars=1000, chunk=c)
            for s in res["sessions"]:
                seen_ids.add(s["session_id"])
        self.assertEqual(len(seen_ids), 5)
        self.assertEqual(p2["chunk"], 2)

    def test_session_signals(self) -> None:
        open_sess = Session(
            app="TestApp",
            session_id="open-1",
            title="Open Thread",
            updated_at=_iso(days_ago=0.2),
            turns=[
                Turn(role="user", text="What if we combine Zettelkasten with voice memos?"),
                Turn(role="assistant", text="Interesting direction."),
                Turn(role="user", text="Could we test this next time?"),
            ],
        ).to_dict()
        sig = open_sess["signals"]
        self.assertEqual(sig["turn_count"], 3)
        self.assertEqual(sig["user_turn_count"], 2)
        self.assertGreater(sig["user_char_ratio"], 0.6)
        self.assertTrue(sig["ends_with_open_question"])

        closed_sess = Session(
            app="TestApp",
            session_id="closed-1",
            title="Closed Chore",
            updated_at=_iso(days_ago=0.2),
            turns=[
                Turn(role="user", text="Fix typo"),
                Turn(role="assistant", text="Done, fixed the typo in README."),
                Turn(role="user", text="Thanks, got it!"),
            ],
        ).to_dict()
        self.assertFalse(closed_sess["signals"]["ends_with_open_question"])


class TestAllAdaptersAndCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        self.now = datetime.now(timezone.utc)
        self.since = self.now - timedelta(days=7)
        self.until = self.now + timedelta(minutes=5)
        self.recent_ts = _iso(days_ago=1)
        self.old_ts = _iso(days_ago=20)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_local_and_hybrid_adapters(self) -> None:
        # 1. Antigravity (1 in-window, 1 old)
        _write_jsonl(
            self.home / ".gemini/antigravity/brain/conv-agy-1/.system_generated/logs/transcript.jsonl",
            [
                {"author": "user", "text": "Explore local-first spaced repetition ideas", "createTime": self.recent_ts},
                {"author": "model", "text": "We could combine FSRS with voice memos.", "createTime": self.recent_ts},
            ],
        )
        _write_jsonl(
            self.home / ".gemini/antigravity/brain/conv-agy-old/.system_generated/logs/transcript.jsonl",
            [{"author": "user", "text": "Old idea", "createTime": self.old_ts}],
            mtime_days_ago=20,
        )

        # 2. Claude Code (including tool_use noise block)
        _write_jsonl(
            self.home / ".claude/projects/my-proj/sess-cc.jsonl",
            [
                {"type": "user", "message": {"role": "user", "content": "What if we build a podcast summarizer?"}, "timestamp": self.recent_ts},
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "tool_use", "name": "bash", "input": {"cmd": "ls"}},
                            {"type": "text", "text": "Let's design the chapter-detection pipeline."},
                        ],
                    },
                    "timestamp": self.recent_ts,
                },
            ],
        )

        # 3. Codex
        _write_jsonl(
            self.home / ".codex/sessions/2026/11/rollout-1.jsonl",
            [
                {"type": "response_item", "payload": {"role": "user", "content": "Build an offline RSS digest"}, "timestamp": self.recent_ts},
                {"type": "response_item", "payload": {"role": "assistant", "content": "Using SQLite FTS5 works great."}, "timestamp": self.recent_ts},
            ],
        )

        # 4. Cursor (JSONL + state.vscdb)
        _write_jsonl(
            self.home / ".cursor/projects/demo/agent-transcripts/cur-1.jsonl",
            [{"role": "user", "content": "Cursor transcript idea", "timestamp": self.recent_ts}],
        )
        cursor_db = self.home / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
        cursor_db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(cursor_db))
        conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO cursorDiskKV VALUES (?, ?)",
            (
                "composerData:abc",
                json.dumps(
                    {
                        "composerId": "cur-db-1",
                        "name": "Cursor Composer Idea",
                        "lastUpdatedAt": self.recent_ts,
                        "bubbles": [
                            {"type": 1, "text": "Design a lightweight keyboard launcher"},
                            {"type": 2, "text": "Start with global hotkey + fuzzy index."},
                        ],
                    }
                ),
            ),
        )
        conn.commit()
        conn.close()

        # 5. Gemini CLI
        gcli_file = self.home / ".gemini/tmp/projhash/chats/session-g1.json"
        gcli_file.parent.mkdir(parents=True, exist_ok=True)
        gcli_file.write_text(
            json.dumps(
                {
                    "sessionId": "gcli-1",
                    "lastUpdated": self.recent_ts,
                    "messages": [
                        {"type": "user", "content": "Try visual poetry with SVG"},
                        {"type": "gemini", "content": "Generate kinetic typography paths."},
                    ],
                }
            ),
            encoding="utf-8",
        )

        # 6. Kimi Code
        _write_jsonl(
            self.home / ".kimi-code/sessions/wk1/s1/context.jsonl",
            [{"role": "user", "content": "Kimi idea: auto-tag reading notes", "timestamp": self.recent_ts}],
        )

        # 7. CodeBuddy
        _write_jsonl(
            self.home / ".codebuddy/projects/p1/cb1.jsonl",
            [{"role": "user", "content": "CodeBuddy idea: CLI flashcards", "timestamp": self.recent_ts}],
        )

        # 8. WorkBuddy (JSONL + SQLite)
        _write_jsonl(
            self.home / ".workbuddy/sessions/wb1.jsonl",
            [{"role": "user", "content": "WorkBuddy weekly planning idea", "timestamp": self.recent_ts}],
        )
        _create_generic_sqlite(self.home / ".workbuddy/index.sqlite", "WorkBuddy", self.recent_ts)

        # 9. Qwen Code / Qoder
        _write_jsonl(
            self.home / ".qwen/projects/qh/q1.jsonl",
            [{"role": "user", "content": "Qwen CLI idea", "timestamp": self.recent_ts}],
        )
        _write_jsonl(
            self.home / ".qoder/projects/qp/qd1.jsonl",
            [{"role": "user", "content": "Qoder idea", "timestamp": self.recent_ts}],
        )

        # 10. QwenWork (JSONL + SQLite + MEMORY.md)
        _write_jsonl(
            self.home / ".qwenworkcn/sessions/qw1.jsonl",
            [{"role": "user", "content": "QwenWork automation spark", "timestamp": self.recent_ts}],
        )
        mem_file = self.home / ".qwenworkcn/awareness/MEMORY.md"
        mem_file.parent.mkdir(parents=True, exist_ok=True)
        mem_file.write_text("# Long-term sparks\n- Explore bilingual corpus alignment", encoding="utf-8")

        # 11. Doubao Work (plaintext .sessions + encrypted DB graceful handling)
        _write_jsonl(
            self.home / ".doubao/agent_mode/workspace/.sessions/db1.jsonl",
            [{"role": "user", "content": "Doubao Work research spark", "timestamp": self.recent_ts}],
        )

        expected_counts = {
            "antigravity": 1,
            "claude-code": 1,
            "codex": 1,
            "cursor": 2,
            "gemini-cli": 1,
            "kimi-code": 1,
            "codebuddy": 1,
            "workbuddy": 2,
            "qwen-qoder": 2,
            "qwenwork": 2,
            "doubao-work": 1,
        }
        for name, count in expected_counts.items():
            rep = ADAPTER_MAP[name].discover(self.since, self.until, home_dir=self.home)
            self.assertEqual(rep.status, "available", f"{name} discover failed: {rep}")
            sessions = ADAPTER_MAP[name].extract(self.since, self.until, home_dir=self.home)
            self.assertEqual(len(sessions), count, f"{name} expected {count} sessions, got {len(sessions)}")

    def test_doubao_encrypted_db_graceful_status(self) -> None:
        enc_db = self.home / "Library/Application Support/DoubaoWork/encrypted.db"
        enc_db.parent.mkdir(parents=True, exist_ok=True)
        enc_db.write_bytes(b"SQLite format 3\x00ENCRYPTED_GARBAGE_BYTES")
        rep = ADAPTER_MAP["doubao-work"].discover(self.since, self.until, home_dir=self.home)
        self.assertEqual(rep.status, "encrypted_or_unreadable")
        sessions = ADAPTER_MAP["doubao-work"].extract(self.since, self.until, home_dir=self.home)
        self.assertEqual(sessions, [])

    def test_hybrid_adapters_skip_noise_dirs(self) -> None:
        # Real WorkBuddy conversations
        _write_jsonl(
            self.home / ".workbuddy/projects/p1/sess1.jsonl",
            [{"role": "user", "content": "Real WorkBuddy project idea", "timestamp": self.recent_ts}],
        )
        _write_jsonl(
            self.home / ".workbuddy/sessions/wb1.jsonl",
            [{"role": "user", "content": "Real WorkBuddy session idea", "timestamp": self.recent_ts}],
        )
        _create_generic_sqlite(self.home / ".workbuddy/index.sqlite", "WorkBuddy", self.recent_ts)

        # Noisy WorkBuddy paths that MUST be ignored
        _write_jsonl(
            self.home / ".workbuddy/projects/p1/tool-results/big.jsonl",
            [{"role": "user", "content": "Tool output noise", "timestamp": self.recent_ts}],
        )
        _write_jsonl(
            self.home / ".workbuddy/projects/p1/subagents/sub.jsonl",
            [{"role": "user", "content": "Subagent noise", "timestamp": self.recent_ts}],
        )
        _write_jsonl(
            self.home / ".workbuddy/logs/telemetry.jsonl",
            [{"role": "user", "content": "Telemetry noise", "timestamp": self.recent_ts}],
        )
        _create_generic_sqlite(self.home / ".workbuddy/cache/c.db", "NoiseCache", self.recent_ts)

        wb_rep = ADAPTER_MAP["workbuddy"].discover(self.since, self.until, home_dir=self.home)
        self.assertEqual(wb_rep.status, "available")
        self.assertEqual(wb_rep.sessions_in_window, 3)
        wb_sessions = ADAPTER_MAP["workbuddy"].extract(self.since, self.until, home_dir=self.home)
        self.assertEqual(len(wb_sessions), 3)

        # Noisy QwenWork & Doubao Work caches MUST be ignored
        _create_generic_sqlite(self.home / ".qwenworkcn/Cache/c.db", "QwenCache", self.recent_ts)
        _write_jsonl(
            self.home / ".doubao/agent_mode/workspace/.sessions/logs/debug.jsonl",
            [{"role": "user", "content": "Debug noise", "timestamp": self.recent_ts}],
        )
        self.assertEqual(ADAPTER_MAP["qwenwork"].discover(self.since, self.until, home_dir=self.home).status, "empty")
        self.assertEqual(ADAPTER_MAP["doubao-work"].discover(self.since, self.until, home_dir=self.home).status, "empty")

    def test_exports_and_markdown_dir(self) -> None:
        export_dir = Path(self.tmp.name) / "exports"
        export_dir.mkdir()

        # Gemini Takeout ZIP (2 turns grouped into 1 conversation by /app/c/conv123)
        takeout_zip = export_dir / "takeout.zip"
        gemini_activity = [
            {
                "header": "Gemini",
                "title": "Prompted: Brainstorm interactive essay formats",
                "titleUrl": "https://gemini.google.com/app/c/conv123",
                "time": self.recent_ts,
                "products": ["Gemini Apps"],
                "details": [{"name": "Details", "value": "Try scrollytelling with inline SVG."}],
            },
            {
                "header": "Gemini",
                "title": "Prompted: How to keep it lightweight?",
                "titleUrl": "https://gemini.google.com/app/c/conv123",
                "time": self.recent_ts,
                "products": ["Gemini Apps"],
                "details": [{"name": "Details", "value": "Use vanilla JS + single HTML file."}],
            },
        ]
        with zipfile.ZipFile(takeout_zip, "w") as zf:
            zf.writestr("Takeout/My Activity/Gemini Apps/MyActivity.json", json.dumps(gemini_activity))

        g_sessions = ADAPTER_MAP["gemini-takeout"].extract(self.since, self.until, extra_paths=[takeout_zip])
        self.assertEqual(len(g_sessions), 1)
        self.assertEqual(g_sessions[0].session_id, "conv123")
        self.assertEqual(len(g_sessions[0].turns), 4)
        self.assertEqual(g_sessions[0].url, "https://gemini.google.com/app/c/conv123")

        # ChatGPT Export JSON
        ts_epoch = datetime.now(timezone.utc).timestamp() - 3600
        cgpt_json = export_dir / "chatgpt_conversations.json"
        cgpt_json.write_text(
            json.dumps(
                [
                    {
                        "id": "cg-1",
                        "title": "Micro-SaaS Idea",
                        "update_time": ts_epoch,
                        "mapping": {
                            "n1": {
                                "message": {
                                    "author": {"role": "user"},
                                    "create_time": ts_epoch - 10,
                                    "content": {"parts": ["Build a PDF table diff tool"]},
                                }
                            },
                            "n2": {
                                "message": {
                                    "author": {"role": "assistant"},
                                    "create_time": ts_epoch,
                                    "content": {"parts": ["Great niche for legal/finance teams."]},
                                }
                            },
                        },
                    }
                ]
            ),
            encoding="utf-8",
        )
        cg_sessions = ADAPTER_MAP["chatgpt-export"].extract(self.since, self.until, extra_paths=[cgpt_json])
        self.assertEqual(len(cg_sessions), 1)
        self.assertEqual(cg_sessions[0].title, "Micro-SaaS Idea")

        # Claude Export JSON
        claude_json = export_dir / "claude_conversations.json"
        claude_json.write_text(
            json.dumps(
                [
                    {
                        "uuid": "cl-1",
                        "name": "Multimodal Corpus Idea",
                        "updated_at": self.recent_ts,
                        "chat_messages": [
                            {"sender": "human", "text": "Align subtitle timing with prosody", "created_at": self.recent_ts},
                            {"sender": "assistant", "text": "Use forced alignment + pitch contour.", "created_at": self.recent_ts},
                        ],
                    }
                ]
            ),
            encoding="utf-8",
        )
        cl_sessions = ADAPTER_MAP["claude-export"].extract(self.since, self.until, extra_paths=[claude_json])
        self.assertEqual(len(cl_sessions), 1)
        self.assertEqual(cl_sessions[0].title, "Multimodal Corpus Idea")

        # Markdown directory
        md_dir = Path(self.tmp.name) / "notes"
        md_dir.mkdir()
        (md_dir / "spark.md").write_text("# Unexplored App Idea\nBuild a calm reading tracker.", encoding="utf-8")
        md_sessions = ADAPTER_MAP["markdown-dir"].extract(self.since, self.until, extra_paths=[md_dir])
        self.assertEqual(len(md_sessions), 1)
        self.assertEqual(md_sessions[0].title, "Unexplored App Idea")

    def test_cli_discover_extract_and_inspect(self) -> None:
        fake_key = "sk-proj-" + "1234567890abcdefghij1234"
        _write_jsonl(
            self.home / ".claude/projects/demo/s1.jsonl",
            [{"role": "user", "content": f"Secret key {fake_key}", "timestamp": self.recent_ts}],
        )

        disc = subprocess.run(
            [sys.executable, str(HISTORY_TOOL), "discover", "--days", "7", "--home", str(self.home), "--format", "json"],
            capture_output=True,
            text=True,
            check=True,
        )
        disc_data = json.loads(disc.stdout)
        cc_rep = next(r for r in disc_data["sources"] if r["source"] == "claude-code")
        self.assertEqual(cc_rep["status"], "available")
        self.assertEqual(cc_rep["sessions_in_window"], 1)

        ext = subprocess.run(
            [
                sys.executable,
                str(HISTORY_TOOL),
                "extract",
                "--days",
                "7",
                "--sources",
                "claude-code",
                "--home",
                str(self.home),
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        ext_data = json.loads(ext.stdout)
        self.assertEqual(ext_data["total_sessions"], 1)
        turn_text = ext_data["sessions"][0]["turns"][0]["text"]
        self.assertIn("[REDACTED]", turn_text)
        self.assertNotIn(fake_key, turn_text)

        insp = subprocess.run(
            [sys.executable, str(HISTORY_TOOL), "inspect", "--path", str(self.home / ".claude/projects/demo/s1.jsonl")],
            capture_output=True,
            text=True,
            check=True,
        )
        insp_data = json.loads(insp.stdout)
        self.assertEqual(insp_data["inspected"][0]["type"], "jsonl")
        self.assertIn("content", insp_data["inspected"][0]["sample_keys"])

    def test_real_antigravity_transcript_schema_and_tag_cleanup(self) -> None:
        _write_jsonl(
            self.home / ".gemini/antigravity/brain/conv-real-agy/.system_generated/logs/transcript.jsonl",
            [
                {
                    "type": "USER_INPUT",
                    "content": "<USER_REQUEST>\n试跑一下 ruminate\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\nlocal time: 2026-11-27\n</ADDITIONAL_METADATA>",
                    "created_at": self.recent_ts,
                },
                {
                    "type": "USER_SETTINGS_CHANGE",
                    "content": "<USER_SETTINGS_CHANGE>Model changed</USER_SETTINGS_CHANGE>",
                    "created_at": self.recent_ts,
                },
                {
                    "type": "PLANNER_RESPONSE",
                    "content": None,
                    "tool_calls": [{"name": "run_command"}],
                    "created_at": self.recent_ts,
                },
                {
                    "type": "SYSTEM_MESSAGE",
                    "content": "<SYSTEM_MESSAGE>internal</SYSTEM_MESSAGE>",
                    "created_at": self.recent_ts,
                },
                {
                    "type": "PLANNER_RESPONSE",
                    "content": "发现 3 个真实兼容性问题。",
                    "created_at": self.recent_ts,
                },
            ],
        )
        sessions = ADAPTER_MAP["antigravity"].extract(self.since, self.until, home_dir=self.home)
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s.title, "试跑一下 ruminate")
        self.assertEqual(len(s.turns), 2)
        self.assertEqual(s.turns[0].role, "user")
        self.assertEqual(s.turns[0].text, "试跑一下 ruminate")
        self.assertEqual(s.turns[1].role, "assistant")
        self.assertEqual(s.turns[1].text, "发现 3 个真实兼容性问题。")

    def test_permission_error_resilience_exists_and_listdir(self) -> None:
        brain_dir = self.home / ".gemini/antigravity/brain"
        brain_dir.mkdir(parents=True)
        codex_dir = self.home / ".codex/sessions"
        codex_dir.mkdir(parents=True)

        # Case 1: exists() raises PermissionError on codex
        orig_exists = Path.exists

        def fake_exists(p: Path) -> bool:
            if ".codex" in str(p):
                raise PermissionError(1, "Operation not permitted", str(p))
            return orig_exists(p)

        with mock.patch.object(Path, "exists", fake_exists):
            rep = ADAPTER_MAP["codex"].discover(self.since, self.until, home_dir=self.home)
            self.assertEqual(rep.status, "permission_denied")

        # Case 2: exists() is True but os.listdir() raises PermissionError on antigravity brain/
        orig_listdir = os.listdir

        def fake_listdir(p):
            if "antigravity/brain" in str(p):
                raise PermissionError(1, "Operation not permitted", str(p))
            return orig_listdir(p)

        with mock.patch("adapters.base.os.listdir", side_effect=fake_listdir):
            rep_agy = ADAPTER_MAP["antigravity"].discover(self.since, self.until, home_dir=self.home)
            self.assertEqual(rep_agy.status, "permission_denied")


if __name__ == "__main__":
    unittest.main()
