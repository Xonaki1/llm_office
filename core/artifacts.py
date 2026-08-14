"""Artifact extraction.

Agents produce deliverables as fenced blocks. Two forms are recognised:

    ```python path=src/app.py
    ...
    ```

    <artifact path="docs/spec.md" kind="markdown">
    ...
    </artifact>

Anything without a path is ordinary prose and is left in the transcript. This is
deliberately strict: guessing filenames from unlabeled code blocks produces a
mess of `snippet_1.py` files that nobody can use.

Extraction is pure — it never touches the database — so it is cheap to test and
safe to run on untrusted model output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_ARTIFACT_BYTES = 1_000_000
MAX_PATH_LENGTH = 400

_FENCE = re.compile(
    r"^```(?P<lang>[A-Za-z0-9_+\-]*)[ \t]+(?P<attrs>[^\n]*path=[^\n]*)\n"
    r"(?P<body>.*?)(?:\n)?^```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)

_TAG = re.compile(
    r"<artifact\s+(?P<attrs>[^>]*)>\n?(?P<body>.*?)</artifact>",
    re.DOTALL | re.IGNORECASE,
)

_ATTR = re.compile(r"(?P<key>[A-Za-z_]+)\s*=\s*\"?(?P<value>[^\"\s]+)\"?")

_LANG_KIND = {
    "md": "markdown",
    "markdown": "markdown",
    "json": "json",
    "yaml": "yaml",
    "yml": "yaml",
    "csv": "csv",
    "sql": "sql",
    "html": "html",
    "txt": "text",
    "": "text",
}


class UnsafeArtifactPath(ValueError):
    pass


@dataclass(frozen=True)
class ExtractedArtifact:
    path: str
    kind: str
    language: str | None
    content: str

    @property
    def size_bytes(self) -> int:
        return len(self.content.encode())


def _parse_attrs(text: str) -> dict[str, str]:
    return {m.group("key").lower(): m.group("value") for m in _ATTR.finditer(text)}


def sanitise_path(raw: str) -> str:
    """Normalise a model-supplied path into a safe relative path.

    Model output is untrusted input. Absolute paths, drive letters, `..`
    traversal and NUL bytes are all rejected rather than cleaned, because a
    "cleaned" path that still resolves somewhere unexpected is worse than an
    error the agent can see and correct.
    """
    path = raw.strip().strip("\"'").replace("\\", "/")
    if not path:
        raise UnsafeArtifactPath("empty artifact path")
    if len(path) > MAX_PATH_LENGTH:
        raise UnsafeArtifactPath("artifact path is too long")
    if "\x00" in path:
        raise UnsafeArtifactPath("artifact path contains a NUL byte")
    if path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        raise UnsafeArtifactPath(f"absolute artifact path is not allowed: {raw!r}")

    parts: list[str] = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise UnsafeArtifactPath(f"path traversal is not allowed: {raw!r}")
        parts.append(part)
    if not parts:
        raise UnsafeArtifactPath(f"artifact path resolves to nothing: {raw!r}")
    return "/".join(parts)


def _kind_for(language: str | None, path: str) -> str:
    if language:
        lowered = language.lower()
        if lowered in _LANG_KIND:
            return _LANG_KIND[lowered]
        return "code"
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return _LANG_KIND.get(suffix, "text")


def extract(text: str) -> list[ExtractedArtifact]:
    """Pull every labelled artifact out of one agent message.

    Later definitions of the same path win, so an agent that rewrites a file
    within a single message produces one artifact, not two.
    """
    found: dict[str, ExtractedArtifact] = {}

    for match in _TAG.finditer(text):
        attrs = _parse_attrs(match.group("attrs"))
        raw_path = attrs.get("path") or attrs.get("name")
        if not raw_path:
            continue
        try:
            path = sanitise_path(raw_path)
        except UnsafeArtifactPath:
            continue
        language = attrs.get("lang") or attrs.get("language")
        body = match.group("body").strip("\n")
        found[path] = ExtractedArtifact(
            path=path,
            kind=attrs.get("kind") or _kind_for(language, path),
            language=language,
            content=body[:MAX_ARTIFACT_BYTES],
        )

    for match in _FENCE.finditer(text):
        attrs = _parse_attrs(match.group("attrs"))
        raw_path = attrs.get("path")
        if not raw_path:
            continue
        try:
            path = sanitise_path(raw_path)
        except UnsafeArtifactPath:
            continue
        language = match.group("lang") or None
        body = match.group("body").strip("\n")
        found[path] = ExtractedArtifact(
            path=path,
            kind=_kind_for(language, path),
            language=language,
            content=body[:MAX_ARTIFACT_BYTES],
        )

    return list(found.values())


ARTIFACT_INSTRUCTIONS = """\
When you produce a file that later agents or the user will need, emit it as a \
labelled block so it is captured as a versioned artifact:

```python path=src/app.py
<file contents>
```

Use the real relative path you intend the file to have. Prose, reasoning and \
explanation stay outside these blocks. Never use absolute paths or `..`."""
