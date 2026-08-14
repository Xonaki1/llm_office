"""Tool registry.

Agents hold an allowlist of tool names. Resolution happens here so an agent
cannot reference a tool that does not exist or is not enabled on this
deployment, and so the API can reject an unknown name at agent-save time rather
than mid-run.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.config import get_settings
from core.tools.artifacts import EditArtifact, ListArtifacts, ReadArtifact, WriteArtifact
from core.tools.base import SideEffect, Tool, ToolSchema
from core.tools.web import WebFetch, WebSearch

_ALL: list[Tool] = [
    ListArtifacts(),
    ReadArtifact(),
    WriteArtifact(),
    EditArtifact(),
    WebFetch(),
    WebSearch(),
]

_BY_NAME: dict[str, Tool] = {tool.name: tool for tool in _ALL}


@dataclass(frozen=True)
class ToolInfo:
    name: str
    description: str
    side_effect: SideEffect
    available: bool
    unavailable_reason: str | None = None


def is_available(tool: Tool) -> tuple[bool, str | None]:
    """Whether a tool can actually run on this deployment.

    Web search needs a third-party provider; offering it without one would have
    the model spend steps discovering that it never works.
    """
    settings = get_settings()
    if tool.name == "web_search" and settings.search_provider == "none":
        return False, "no search provider is configured on this deployment"
    if tool.name in {"web_fetch", "web_search"} and not settings.tools_network_enabled:
        return False, "network tools are disabled on this deployment"
    return True, None


def catalogue() -> list[ToolInfo]:
    infos: list[ToolInfo] = []
    for tool in _ALL:
        available, reason = is_available(tool)
        infos.append(
            ToolInfo(
                name=tool.name,
                description=tool.description,
                side_effect=tool.side_effect,
                available=available,
                unavailable_reason=reason,
            )
        )
    return infos


def known_names() -> set[str]:
    return set(_BY_NAME)


def get(name: str) -> Tool | None:
    return _BY_NAME.get(name)


def resolve(names: list[str]) -> list[Tool]:
    """Turn an agent's allowlist into runnable tools.

    Unknown or unavailable names are dropped rather than raising: an agent
    saved when a tool was enabled must keep working after it is turned off.
    """
    resolved: list[Tool] = []
    for name in names:
        tool = _BY_NAME.get(name)
        if tool is None:
            continue
        available, _ = is_available(tool)
        if available:
            resolved.append(tool)
    return resolved


def schemas(tools: list[Tool]) -> list[ToolSchema]:
    return [tool.schema for tool in tools]
