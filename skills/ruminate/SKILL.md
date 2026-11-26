---
name: ruminate
description: >-
  Review recent AI conversations (default: past 7 days) across local coding
  agents, hybrid desktop workspaces, and cloud chat exports to surface
  forgotten ideas, unexplored sparks, valuable discussions, and unfinished
  threads. Use when the user asks "what did we discuss", "weekly AI recap",
  "ruminate", "review recent AI chats", "这周和 AI 聊了什么", "回顾最近和 AI
  的讨论", or "找回遗忘的点子".
license: MIT
compatibility: >-
  Requires Python 3.8+ (standard library only, no external packages). Reading
  local app histories outside the current workspace may require user
  permission or sandbox approval.
metadata:
  author: chester
  version: "0.1.0"
---

# Ruminate

Help the user look back across recent AI conversations and recover high-signal ideas, discussions, and half-explored threads without drowning in routine debugging logs.

## Core Rules

1. **Match the user's language**: Always write the final digest and all user-facing messages in the same language the user used to invoke the skill.
2. **Discover before reading**: Never read conversation bodies across multiple apps silently. Always inspect availability first, then get user confirmation unless the user already specified exact sources or `--path` inputs.
3. **Never fabricate or extrapolate**: Only report ideas, conclusions, and quotes that appear in extracted transcripts. If a source is truncated, encrypted, or missing, state that explicitly.
4. **Protect secrets and boundaries**: Never output unredacted credentials, never attempt to decrypt encrypted app databases, and never inject into running processes.
5. **Do not read `scripts/` source code during normal execution**: Invoke `scripts/history_tool.py` directly using the CLI flags documented below.

## Workflow

### Step 1: Discover Available Sources (No Body Read)

Determine the requested time window (default: `--days 7`, or `--since YYYY-MM-DD` / `--until YYYY-MM-DD`). Run:

```bash
python3 scripts/history_tool.py discover --days 7 --format json
```

Supported flags: `--days N`, `--since YYYY-MM-DD`, `--until YYYY-MM-DD`, `--sources <comma-list>`, `--path <file_or_dir>` (repeatable for `.zip`, `.json`, `.html`, or Markdown folders), `--format json|table`.

Present a concise inventory table to the user showing:
- Detected sources and in-window session counts
- Any `permission_denied` paths (blocked by sandbox/OS permissions)
- Any `encrypted_or_unreadable` sources (explain that export/Markdown fallback is needed)

**Sandbox & permission rule**: If `discover` returns `permission_denied` for paths outside the workspace, report the exact blocked paths clearly. If the host agent supports requesting user-approved unsandboxed execution for a single read-only command, offer to rerun that read-only `discover`/`extract` command with user approval first; only suggest running the command manually in the user's terminal when the host cannot request approval or the user prefers manual execution. Never silently skip blocked sources.

Wait for the user to confirm which sources to include (skip waiting only if the user already named the exact sources or paths upfront).

### Step 2: Extract & Paginate

Run `extract` for the approved sources:

```bash
python3 scripts/history_tool.py extract --days 7 --sources <comma-list> --max-chars 80000 --chunk 1 --format json
```

Supported flags: `--days N`, `--since YYYY-MM-DD`, `--until YYYY-MM-DD`, `--sources <comma-list>`, `--path <file_or_dir>` (repeatable), `--max-chars 80000`, `--per-session-chars 4000`, `--chunk N`, `--format json|markdown`.

- The script strips tool outputs, system prompts, and oversized code/diff blocks, redacts common secrets (`[REDACTED]`), and paginates deterministically.
- If `total_chunks > 1`, fetch `--chunk 2`, `--chunk 3`, etc. sequentially (or in parallel if the host supports subagents), keeping per-chunk candidate notes before final synthesis so nothing is dropped.

### Step 3: Optional Pure-Cloud Sources

If the user also wants pure-cloud web chats (Gemini Web, ChatGPT Web, Claude Web) and no export archive was provided, read [references/cloud-sources.md](./references/cloud-sources.md) and follow its 3-tier fallback:
1. Official export archive (`--path`)
2. Read-only browser capture via the host's browser capability (only if available and user-approved; hand off immediately to the user for login, 2FA, or CAPTCHA)
3. 10-second Cloud Bridge Prompt

### Step 4: Synthesize the Digest

Read [references/output-template.md](./references/output-template.md) and format the digest:
- **Default (`sparks` mode — Layout C)**:
  - Filter using the **7 high-value rumination signals** (decision shifts, overturned assumptions/mental-model updates, sharp knowledge gaps, high-leverage inferences, cross-domain bridges, serendipitous tangents, and open loops/latent cross-app tensions) while excluding long debugging marathons, one-off chores, and pure lookups.
  - Merge across all apps by theme (never per-app silos) into:
    1. **Thinking Radar** (cross-app motifs/tensions + coverage count)
    2. **3–7 Memory Cue Cards** (each with a short verbatim user quote, where it landed, a concrete re-entry angle, and `[App | YYYY-MM-DD | Session Title or ID]`)
    3. **Edge Index** + one-line folded routine count (auto-omit empty blocks on quiet weeks).
- **`full` mode**: Only use when the user explicitly asks for a complete/full recap of all sessions.

Deliver the digest **directly in chat by default**. Do not write files into the user's workspace unasked.

### Step 5: Opt-in Persistence

Only save a Markdown file if the user explicitly asks to export or save the digest:
- Default filename: `ruminate-YYYY-MM-DD.md`
- If that file already exists, increment to `ruminate-YYYY-MM-DD-v2.md`, `ruminate-YYYY-MM-DD-v3.md`, etc. Never overwrite an existing digest.

## Extending Adapters

To inspect an unfamiliar local log format or add a new app adapter, see [references/adding-adapters.md](./references/adding-adapters.md).
