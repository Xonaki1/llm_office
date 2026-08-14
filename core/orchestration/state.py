from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentSpec:
    """A snapshot of an Agent row, decoupled from the ORM so the engine can be
    tested without a database."""

    id: str
    name: str
    role: str
    system_prompt: str
    model: str
    effort: str = "medium"
    max_tokens: int = 8000
    tools: list[Any] = field(default_factory=list)


@dataclass
class BoardEntry:
    """One contribution to the shared board. Every topology reads and writes the
    same structure, which is what lets the presets share an engine."""

    agent_id: str
    agent_name: str
    role: str
    content: str
    step_index: int
    visible_to: frozenset[str] | None = None  # None => visible to everyone

    def is_visible_to(self, agent_id: str) -> bool:
        return self.visible_to is None or agent_id in self.visible_to


@dataclass
class ArtifactRef:
    path: str
    version: int
    kind: str
    size_bytes: int
    produced_by: str


@dataclass
class RunState:
    user_input: str
    board: list[BoardEntry] = field(default_factory=list)
    # path -> latest content, so an agent always reads the current version
    artifacts: dict[str, str] = field(default_factory=dict)
    artifact_versions: dict[str, int] = field(default_factory=dict)
    artifact_log: list[ArtifactRef] = field(default_factory=list)
    final_output: str | None = None
    step_index: int = 0
    # Set by a preset when it wants the run recorded as incomplete.
    notes: dict[str, Any] = field(default_factory=dict)

    def append(
        self,
        agent: AgentSpec,
        content: str,
        *,
        visible_to: frozenset[str] | None = None,
    ) -> BoardEntry:
        entry = BoardEntry(
            agent_id=agent.id,
            agent_name=agent.name,
            role=agent.role,
            content=content,
            step_index=self.step_index,
            visible_to=visible_to,
        )
        self.board.append(entry)
        return entry

    def record_artifact(self, path: str, content: str, kind: str, produced_by: str) -> int:
        version = self.artifact_versions.get(path, 0) + 1
        self.artifact_versions[path] = version
        self.artifacts[path] = content
        self.artifact_log.append(
            ArtifactRef(
                path=path,
                version=version,
                kind=kind,
                size_bytes=len(content.encode()),
                produced_by=produced_by,
            )
        )
        return version

    def visible_board(self, agent_id: str) -> list[BoardEntry]:
        return [e for e in self.board if e.is_visible_to(agent_id)]

    def transcript(
        self,
        *,
        agent_id: str | None = None,
        max_chars: int = 400_000,
        keep_recent: int = 6,
    ) -> str:
        """The board rendered for a prompt.

        Long runs will not fit in any context window, so older entries are
        summarised down to a headline while the most recent ones stay verbatim.
        Truncating the *oldest* content preserves the working context an agent
        actually needs, and the artifact table carries anything durable.
        """
        entries = self.visible_board(agent_id) if agent_id else list(self.board)
        if not entries:
            return "(no prior contributions)"

        rendered: list[str] = []
        total = 0
        # Walk newest-first so the recent turns are the ones kept in full.
        for position, entry in enumerate(reversed(entries)):
            header = f"### [{entry.step_index}] {entry.agent_name} ({entry.role})"
            body = entry.content
            if position >= keep_recent or total + len(body) > max_chars:
                body = _headline(body)
            block = f"{header}\n{body}"
            total += len(block)
            rendered.append(block)
            if total > max_chars:
                rendered.append("### (earlier contributions omitted)")
                break
        return "\n\n".join(reversed(rendered))

    def artifact_index(self) -> str:
        if not self.artifacts:
            return "(no artifacts yet)"
        lines = [
            f"- {path} (v{self.artifact_versions[path]}, {len(content.encode())} bytes)"
            for path, content in sorted(self.artifacts.items())
        ]
        return "\n".join(lines)

    def last_output(self) -> str:
        return self.board[-1].content if self.board else self.user_input


def _headline(text: str, limit: int = 600) -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[:limit].rstrip() + f"\n… [{len(stripped) - limit} more characters omitted]"
