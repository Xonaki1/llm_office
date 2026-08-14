"""Artifact tools.

Without these an agent only sees an index of what exists and has to re-emit a
whole file to change one line. With them it can read the current version, make a
targeted edit, and write the result back — which is both cheaper and less prone
to the model silently dropping parts of a file it was asked to "keep".
"""

from __future__ import annotations

from typing import Any

from core.artifacts import UnsafeArtifactPath, sanitise_path
from core.tools.base import (
    SideEffect,
    Tool,
    ToolContext,
    ToolError,
    ToolResult,
    optional_int,
    require_str,
)

MAX_WRITE_CHARS = 400_000


class ListArtifacts(Tool):
    name = "list_artifacts"
    description = (
        "List every file produced so far in this run, with its size and current "
        "version. Use it before reading or writing to see what already exists."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "prefix": {
                "type": "string",
                "description": "Optional path prefix to filter by, e.g. 'src/'.",
            }
        },
        "required": [],
    }
    side_effect = SideEffect.READ_ONLY

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        prefix = arguments.get("prefix") or ""
        if not isinstance(prefix, str):
            raise ToolError("`prefix` must be a string")

        matches = sorted(
            (path, content)
            for path, content in context.artifacts.items()
            if path.startswith(prefix)
        )
        if not matches:
            return ToolResult(
                content="No artifacts yet." if not prefix else f"No artifacts under {prefix!r}.",
                metadata={"count": 0},
            )

        lines = [
            f"{path}  (v{context.read_artifact_version.get(path, 1)}, "
            f"{len(content.encode())} bytes, {content.count(chr(10)) + 1} lines)"
            for path, content in matches
        ]
        return ToolResult(content="\n".join(lines), metadata={"count": len(matches)})


class ReadArtifact(Tool):
    name = "read_artifact"
    description = (
        "Read the current contents of a file produced in this run. Optionally read "
        "a line range instead of the whole file."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path, e.g. 'src/app.py'."},
            "start_line": {
                "type": "integer",
                "description": "First line to return, 1-indexed. Defaults to the start.",
            },
            "line_count": {
                "type": "integer",
                "description": "How many lines to return. Defaults to the whole file.",
            },
        },
        "required": ["path"],
    }
    side_effect = SideEffect.READ_ONLY

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        path = _safe_path(require_str(arguments, "path", max_length=400))
        content = context.artifacts.get(path)
        if content is None:
            available = ", ".join(sorted(context.artifacts)[:20]) or "(none)"
            raise ToolError(f"No artifact at {path!r}. Existing artifacts: {available}")

        lines = content.split("\n")
        if "start_line" not in arguments and "line_count" not in arguments:
            return ToolResult(
                content=content,
                metadata={"path": path, "lines": len(lines)},
            )

        start = optional_int(arguments, "start_line", default=1, low=1, high=len(lines))
        count = optional_int(
            arguments, "line_count", default=len(lines), low=1, high=len(lines)
        )
        window = lines[start - 1 : start - 1 + count]
        numbered = "\n".join(f"{start + i}\t{line}" for i, line in enumerate(window))
        return ToolResult(
            content=numbered,
            metadata={"path": path, "start_line": start, "returned_lines": len(window)},
        )


class WriteArtifact(Tool):
    name = "write_artifact"
    description = (
        "Create or replace a file in this run. Always write the complete file — "
        "the contents replace the previous version rather than merging with it. "
        "Each write creates a new version; earlier versions are kept."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path, e.g. 'src/app.py'."},
            "content": {"type": "string", "description": "The complete file contents."},
            "kind": {
                "type": "string",
                "description": "Optional content kind: code, markdown, json, text.",
            },
        },
        "required": ["path", "content"],
    }
    side_effect = SideEffect.WRITE

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        path = _safe_path(require_str(arguments, "path", max_length=400))

        content = arguments.get("content")
        if not isinstance(content, str):
            raise ToolError("`content` is required and must be a string")
        if len(content) > MAX_WRITE_CHARS:
            raise ToolError(
                f"`content` is {len(content)} characters; the limit is {MAX_WRITE_CHARS}. "
                f"Split the file into smaller ones."
            )

        kind = arguments.get("kind")
        if kind is not None and not isinstance(kind, str):
            raise ToolError("`kind` must be a string")

        version = await context.write_artifact(path, content, kind or _kind_for(path))
        return ToolResult(
            content=f"Wrote {path} (v{version}, {len(content.encode())} bytes).",
            metadata={"path": path, "version": version, "size_bytes": len(content.encode())},
        )


class EditArtifact(Tool):
    name = "edit_artifact"
    description = (
        "Replace an exact substring in an existing file. Cheaper and safer than "
        "rewriting the whole file. The old text must appear exactly once."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path of the file to edit."},
            "old_text": {
                "type": "string",
                "description": "Exact text to replace, including indentation. Must be unique.",
            },
            "new_text": {"type": "string", "description": "Replacement text."},
        },
        "required": ["path", "old_text", "new_text"],
    }
    side_effect = SideEffect.WRITE

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        path = _safe_path(require_str(arguments, "path", max_length=400))
        old_text = require_str(arguments, "old_text", max_length=MAX_WRITE_CHARS)
        new_text = arguments.get("new_text")
        if not isinstance(new_text, str):
            raise ToolError("`new_text` is required and must be a string")

        content = context.artifacts.get(path)
        if content is None:
            raise ToolError(f"No artifact at {path!r}.")

        occurrences = content.count(old_text)
        if occurrences == 0:
            raise ToolError(
                f"`old_text` does not appear in {path}. Read the file and copy the "
                f"text exactly, including indentation."
            )
        if occurrences > 1:
            raise ToolError(
                f"`old_text` appears {occurrences} times in {path}; it must be unique. "
                f"Include more surrounding context to disambiguate."
            )

        updated = content.replace(old_text, new_text, 1)
        version = await context.write_artifact(path, updated, _kind_for(path))
        return ToolResult(
            content=f"Edited {path} (v{version}).",
            metadata={"path": path, "version": version},
        )


def _safe_path(raw: str) -> str:
    try:
        return sanitise_path(raw)
    except UnsafeArtifactPath as exc:
        raise ToolError(str(exc)) from exc


_EXTENSION_KINDS = {
    "md": "markdown",
    "markdown": "markdown",
    "json": "json",
    "yaml": "yaml",
    "yml": "yaml",
    "csv": "csv",
    "txt": "text",
    "sql": "sql",
    "html": "html",
}


def _kind_for(path: str) -> str:
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return _EXTENSION_KINDS.get(suffix, "code" if suffix else "text")
