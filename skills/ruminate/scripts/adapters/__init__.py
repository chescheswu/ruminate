"""Adapter registry for ruminate."""

from typing import Dict, List

from .antigravity import AntigravityAdapter
from .base import BaseAdapter
from .claude_code import ClaudeCodeAdapter
from .codebuddy import CodeBuddyAdapter
from .codex import CodexAdapter
from .cursor import CursorAdapter
from .doubao_work import DoubaoWorkAdapter
from .exports import ChatGPTExportAdapter, ClaudeExportAdapter, GeminiTakeoutAdapter
from .gemini_cli import GeminiCliAdapter
from .kimi_code import KimiCodeAdapter
from .markdown_dir import MarkdownDirAdapter
from .qwen_qoder import QwenQoderAdapter
from .qwenwork import QwenWorkAdapter
from .workbuddy import WorkBuddyAdapter

ALL_ADAPTERS: List[BaseAdapter] = [
    AntigravityAdapter(),
    ClaudeCodeAdapter(),
    CodexAdapter(),
    CursorAdapter(),
    GeminiCliAdapter(),
    KimiCodeAdapter(),
    CodeBuddyAdapter(),
    WorkBuddyAdapter(),
    QwenQoderAdapter(),
    QwenWorkAdapter(),
    DoubaoWorkAdapter(),
    GeminiTakeoutAdapter(),
    ChatGPTExportAdapter(),
    ClaudeExportAdapter(),
    MarkdownDirAdapter(),
]

ADAPTER_MAP: Dict[str, BaseAdapter] = {a.name: a for a in ALL_ADAPTERS}
