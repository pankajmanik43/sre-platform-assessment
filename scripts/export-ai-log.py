#!/usr/bin/env python3
"""Render Claude Code session JSONL transcripts into readable markdown.

Reads this project's session logs from ~/.claude/projects/<slug>/*.jsonl and
writes one markdown file per session into ai-log/. User/assistant prose is kept;
tool calls and results are summarized and truncated; thinking is collapsed.

Usage: python3 scripts/export-ai-log.py
"""
import json
import os
import re
import sys
from pathlib import Path

SLUG = "-home-hp-sre-platform-assessment"
SRC = Path.home() / ".claude" / "projects" / SLUG
OUT = Path(__file__).resolve().parent.parent / "ai-log"

TOOL_INPUT_CAP = 800
TOOL_RESULT_CAP = 1200
THINKING_CAP = 6000
TEXT_CAP = 12000
SECRET = re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}")


def redact(s: str) -> str:
    return SECRET.sub("sk-ant-***REDACTED***", s)


def trunc(s: str, cap: int) -> str:
    s = s.rstrip()
    if len(s) <= cap:
        return s
    return s[:cap] + f"\n… [truncated {len(s) - cap} chars]"


def summarize_tool_input(name: str, inp: dict) -> str:
    if not isinstance(inp, dict):
        return trunc(str(inp), TOOL_INPUT_CAP)
    for key in ("command", "file_path", "path", "pattern", "query", "prompt", "skill"):
        if key in inp:
            return trunc(str(inp[key]), TOOL_INPUT_CAP)
    return trunc(json.dumps(inp)[:TOOL_INPUT_CAP], TOOL_INPUT_CAP)


def block_to_md(b) -> str:
    if isinstance(b, str):
        return trunc(b, TEXT_CAP)
    t = b.get("type")
    if t == "text":
        return trunc(b.get("text", ""), TEXT_CAP)
    if t == "thinking":
        body = trunc(b.get("thinking", ""), THINKING_CAP)
        if not body.strip():
            return ""
        return f"<details><summary>💭 thinking</summary>\n\n{body}\n\n</details>"
    if t == "tool_use":
        inp = summarize_tool_input(b.get("name", ""), b.get("input", {}))
        return f"🔧 **{b.get('name')}**\n```\n{inp}\n```"
    if t == "tool_result":
        c = b.get("content", "")
        if isinstance(c, list):
            c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
        flag = " ⚠️ error" if b.get("is_error") else ""
        return f"↳ _result{flag}_\n```\n{trunc(str(c), TOOL_RESULT_CAP)}\n```"
    return ""


def render(path: Path) -> str:
    title = None
    parts = []
    for line in path.read_text().splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = d.get("type")
        if t == "ai-title" and not title:
            title = d.get("title") or d.get("aiTitle")
        if t not in ("user", "assistant"):
            continue
        msg = d.get("message") or {}
        role = msg.get("role", t)
        content = msg.get("content")
        blocks = content if isinstance(content, list) else [content]
        rendered = [block_to_md(b) for b in blocks]
        rendered = [r for r in rendered if r.strip()]
        if not rendered:
            continue
        heading = "### 👤 User" if role == "user" else "### 🤖 Assistant"
        parts.append(heading + "\n\n" + "\n\n".join(rendered))
    header = f"# {title or path.stem}\n\n_Session `{path.stem}` — rendered from Claude Code transcript._\n"
    return redact(header + "\n" + "\n\n---\n\n".join(parts) + "\n")


def main() -> None:
    if not SRC.is_dir():
        sys.exit(f"no session dir at {SRC}")
    OUT.mkdir(exist_ok=True)
    files = sorted(SRC.glob("*.jsonl"))
    if not files:
        sys.exit(f"no .jsonl sessions in {SRC}")
    for f in files:
        md = render(f)
        dst = OUT / f"{f.stem}.md"
        dst.write_text(md)
        print(f"wrote {dst.relative_to(OUT.parent)} ({len(md)} chars)")


if __name__ == "__main__":
    main()
