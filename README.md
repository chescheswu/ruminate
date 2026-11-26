# ruminate

A cross-agent [Agent Skills](https://agentskills.io) skill for periodically reviewing what you discussed with AI across multiple local coding agents, hybrid desktop workspaces, and cloud chat exports—surfacing forgotten ideas, unexplored sparks, valuable discussions, and unfinished threads.

## Why `ruminate`?

Most AI recaps dump raw logs or focus on debugging chores. `ruminate` is built for human reflection:

- **Sparks-first signal filtering**: Aggressively folds routine bug fixes, lint chores, and mechanical shell tasks so novel ideas, side-project seeds, and half-explored hypotheses stand out.
- **Multi-app & hybrid coverage**: Scans 11 local/hybrid AI tools plus official cloud exports and arbitrary Markdown folders via pluggable adapters.
- **Zero-dependency Python 3.8+**: Uses only the Python standard library (`json`, `sqlite3`, `zipfile`, `re`, `pathlib`, `argparse`). Works offline across macOS, Linux, and Windows.
- **Privacy & context-window safe**: Two-stage `discover` → consent → `extract` workflow, automatic secret redaction (`[REDACTED]`), tool-output stripping, and deterministic chunk pagination.

## Installation

### Via `npx skills` (Recommended)

```bash
npx skills add chescheswu/ruminate
# Or target the skill explicitly:
npx skills add chescheswu/ruminate --skill ruminate
```

### Manual Installation

Copy `skills/ruminate/` into your agent's skills directory:

| Agent / IDE | User-global path | Project-local path |
| :--- | :--- | :--- |
| **Antigravity** | `~/.gemini/config/skills/ruminate/` | `.agents/skills/ruminate/` |
| **Claude Code** | `~/.claude/skills/ruminate/` | `.claude/skills/ruminate/` |
| **Codex CLI** | `~/.agents/skills/ruminate/` | `.agents/skills/ruminate/` |
| **Cursor** | `~/.cursor/skills/ruminate/` | `.cursor/skills/ruminate/` |
| **Gemini CLI** | `~/.gemini/skills/ruminate/` | `.gemini/skills/ruminate/` |

## Supported Sources

### Local & Hybrid Workspaces (Built-in)

| Source ID | App | Default Local Paths |
| :--- | :--- | :--- |
| `antigravity` | Antigravity | `~/.gemini/antigravity/brain/*/.system_generated/logs/transcript.jsonl` |
| `claude-code` | Claude Code | `~/.claude/projects/*/*.jsonl` (`$CLAUDE_CONFIG_DIR`) |
| `codex` | Codex CLI | `~/.codex/sessions/**/*.jsonl` (`$CODEX_HOME`) |
| `cursor` | Cursor | `~/.cursor/projects/*/agent-transcripts/*.jsonl` & `state.vscdb` |
| `gemini-cli` | Gemini CLI | `~/.gemini/tmp/*/chats/*.json(l)` & `~/.config/gemini-cli/sessions/` |
| `kimi-code` | Kimi Code | `~/.kimi-code/sessions/**` & `~/.kimi/sessions/**` |
| `codebuddy` | CodeBuddy | `~/.codebuddy/projects/**/*.jsonl` & `history.jsonl` |
| `workbuddy` | WorkBuddy | `~/.workbuddy/` & `~/.workbuddy-ai/` (JSONL + read-only SQLite) |
| `qwen-qoder` | Qwen Code / Qoder | `~/.qwen/**` & `~/.qoder/projects/**/*.jsonl` |
| `qwenwork` | QwenWork (千问办公) | `~/.qwenworkcn/`, `~/.qwenwork/`, `QwenWorkCN/` (JSONL, SQLite, `MEMORY.md`) |
| `doubao-work` | Doubao Work (豆包工作) | `~/.doubao/agent_mode/workspace/.sessions`, `DoubaoWork/` |

> **Encrypted or cloud-only caches**: `ruminate` opens SQLite databases strictly in read-only mode (`?mode=ro`) and never attempts decryption or process injection. If an app's local cache is encrypted or cloud-only, `discover` reports `encrypted_or_unreadable` and guides you to the export fallback.

### Cloud Chat Exports & Pure-Cloud Workflow

- **Official Export Adapters (`--path`)**:
  - `gemini-takeout`: Google Takeout `My Activity/Gemini Apps/MyActivity.json` (or `.zip` / `.html` fallback), automatically grouping flat activity events by `/app/c/<conversation_id>`.
  - `chatgpt-export`: OpenAI `conversations.json` (or `.zip`).
  - `claude-export`: Anthropic `conversations.json` (or `.zip`).
  - `markdown-dir`: Any directory of `.md` / `.txt` notes.
- **3-Tier Pure-Cloud Fallback** (see [`skills/ruminate/references/cloud-sources.md`](skills/ruminate/references/cloud-sources.md)):
  1. Official export archive (`--path`).
  2. Host-agnostic read-only browser capture (e.g., `https://myactivity.google.com/product/gemini`) with human handoff for login/2FA/CAPTCHA.
  3. 10-second Cloud Bridge Prompt for pasting a self-summary from web chat.

## CLI Quick Reference

All commands run from `skills/ruminate/`:

```bash
# 1. Discover available sources and session counts (no chat bodies read)
python3 scripts/history_tool.py discover --days 7 --format table

# 2. Extract redacted, noise-stripped, paginated transcripts
python3 scripts/history_tool.py extract --days 7 --sources claude-code,codex --max-chars 80000 --chunk 1

# 3. Inspect an unknown JSON/JSONL/SQLite schema safely (structure only, no secrets)
python3 scripts/history_tool.py inspect --path /path/to/session_or_db
```

## Privacy & Sandbox Model

1. **Discover before reading**: `discover` checks path existence, file counts, and byte sizes without dumping conversation text.
2. **Explicit consent**: The agent presents the discovered inventory and waits for confirmation (unless you already named the sources/paths).
3. **Sandbox transparency**: Reading `~/.claude`, `~/.codex`, or `~/Library/Application Support/...` crosses workspace boundaries. If your agent's sandbox blocks access, `ruminate` reports the exact blocked paths so you can approve access or run the script in your own terminal.
4. **Local redaction**: Common API keys, bearer tokens, PEM private keys, and `.env` secrets are replaced with `[REDACTED]` before entering the LLM context.

## Running Tests

```bash
python3 -m unittest discover -s tests -v
```

## License

[MIT](LICENSE)
