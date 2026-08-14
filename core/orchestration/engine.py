from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from core import events as ev
from core.artifacts import ARTIFACT_INSTRUCTIONS, extract
from core.events import EventEmitter, TokenBuffer
from core.llm.providers import (
    ContextWindowExceeded,
    Message,
    RefusalError,
    ToolCallRequest,
    ToolResultPart,
    ToolSpec,
)
from core.llm.router import LLMRouter
from core.orchestration.budget import BudgetGuard
from core.orchestration.state import AgentSpec, RunState
from core.tools import SideEffect, Tool, ToolContext, ToolResult

log = structlog.get_logger(__name__)


@dataclass
class StepRecord:
    """One agent turn. A turn with tools spans several model calls; the usage of
    all of them is summed here, because the user thinks in turns, not calls."""

    index: int
    agent: AgentSpec
    instruction: str
    output: str
    provider: str
    tokens_in: int
    tokens_out: int
    cached_tokens: int
    reasoning_tokens: int
    cost_microcents: int
    billable_microcents: int
    latency_ms: int
    attempts: int
    stop_reason: str | None
    model_calls: int = 1
    tool_calls: int = 0


@dataclass
class ToolCallRecord:
    step_index: int
    call_index: int
    agent_id: str
    agent_name: str
    tool: str
    arguments: dict[str, Any]
    result: str
    is_error: bool
    latency_ms: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ArtifactRecord:
    path: str
    version: int
    kind: str
    language: str | None
    content: str
    step_index: int
    agent_name: str


StepSink = Callable[[StepRecord], Awaitable[None]]
ArtifactSink = Callable[[ArtifactRecord], Awaitable[None]]
ToolCallSink = Callable[[ToolCallRecord], Awaitable[None]]


class Engine:
    """Executes individual agent turns.

    Topologies (see presets.py) decide *who* speaks next; the engine owns budget
    enforcement, cancellation, streaming, the tool-calling loop, artifact
    capture, event emission and persistence. Splitting it this way means a new
    topology is one function and needs no knowledge of any of that.
    """

    def __init__(
        self,
        *,
        llm: LLMRouter,
        emitter: EventEmitter,
        budget: BudgetGuard,
        step_sink: StepSink | None = None,
        artifact_sink: ArtifactSink | None = None,
        tool_call_sink: ToolCallSink | None = None,
        tool_resolver: Callable[[AgentSpec], list[Tool]] | None = None,
        markup: Callable[[int], int] | None = None,
        max_board_chars: int = 400_000,
        step_timeout_seconds: float = 600.0,
        max_tool_iterations: int = 8,
        max_tool_calls_per_turn: int = 6,
        stream_tokens: bool = True,
    ) -> None:
        self._llm = llm
        self._emitter = emitter
        self._budget = budget
        self._step_sink = step_sink
        self._artifact_sink = artifact_sink
        self._tool_call_sink = tool_call_sink
        # Topologies never mention tools; the engine resolves each agent's own
        # allowlist, so enabling a tool is an agent-level decision.
        self._tool_resolver = tool_resolver or _default_tool_resolver
        self._markup = markup or (lambda value: value)
        self._max_board_chars = max_board_chars
        self._step_timeout = step_timeout_seconds
        self._max_tool_iterations = max_tool_iterations
        self._max_tool_calls_per_turn = max_tool_calls_per_turn
        self._stream_tokens = stream_tokens

    @property
    def budget(self) -> BudgetGuard:
        return self._budget

    async def call_agent(
        self,
        agent: AgentSpec,
        state: RunState,
        instruction: str,
        *,
        json_mode: bool = False,
        private_to: frozenset[str] | None = None,
        tools: list[Tool] | None = None,
    ) -> str:
        await self._budget.check_before_step()
        state.step_index += 1
        index = state.step_index

        # A turn asking for JSON must not also offer tools: the model would call
        # a tool instead of answering, and the caller is waiting for an object.
        requested_tools = self._tool_resolver(agent) if tools is None else tools
        active_tools = [] if json_mode else requested_tools
        tool_by_name = {tool.name: tool for tool in active_tools}
        tool_specs = [
            ToolSpec(name=t.name, description=t.description, parameters=t.parameters)
            for t in active_tools
        ]

        await self._emitter.emit(
            ev.STEP_START,
            step=index,
            agent_id=agent.id,
            agent_name=agent.name,
            role=agent.role,
            model=agent.model,
            effort=agent.effort,
            instruction=instruction,
            tools=[t.name for t in active_tools],
        )

        system = self._system_prompt(agent, active_tools)
        conversation = [
            Message(role="user", content=self._user_message(agent, state, instruction))
        ]

        buffer = TokenBuffer(self._emitter, index)
        started = time.perf_counter()

        totals = {
            "tokens_in": 0,
            "tokens_out": 0,
            "cached": 0,
            "reasoning": 0,
            "cost": 0,
            "billable": 0,
        }
        text_parts: list[str] = []
        provider = ""
        stop_reason: str | None = None
        attempts = 1
        model_calls = 0
        tool_call_count = 0

        try:
            for iteration in range(self._max_tool_iterations + 1):
                # The ceiling is checked before *every* model call, not just the
                # first: a tool loop is exactly where a run runs away.
                if iteration > 0:
                    await self._budget.check_before_step()

                # On the last permitted iteration the tools are withdrawn, which
                # forces the model to answer with what it has instead of asking
                # for another call it will not get.
                last_chance = iteration == self._max_tool_iterations
                routed = await self._complete(
                    agent=agent,
                    system=system,
                    conversation=conversation,
                    state=state,
                    instruction=instruction,
                    json_mode=json_mode,
                    tools=[] if last_chance else tool_specs,
                    buffer=buffer,
                    first_call=iteration == 0,
                )
                model_calls += 1
                result = routed.result
                provider = result.provider
                stop_reason = result.stop_reason
                attempts = max(attempts, routed.attempts)

                totals["tokens_in"] += result.usage.input_tokens
                totals["tokens_out"] += result.usage.output_tokens
                totals["cached"] += result.usage.cached_input_tokens
                totals["reasoning"] += result.usage.reasoning_tokens
                totals["cost"] += routed.cost_microcents
                if routed.billed_to_platform:
                    totals["billable"] += self._markup(routed.cost_microcents)
                self._budget.record(routed.cost_microcents)

                if result.text:
                    text_parts.append(result.text)

                if not result.tool_calls:
                    break

                requested = result.tool_calls[: self._max_tool_calls_per_turn]
                if len(result.tool_calls) > len(requested):
                    log.info(
                        "engine.tool_calls_capped",
                        step=index,
                        requested=len(result.tool_calls),
                        allowed=len(requested),
                    )

                conversation.append(
                    Message(
                        role="assistant", content=result.text, tool_calls=requested
                    )
                )
                results = await self._run_tools(
                    requested, tool_by_name, agent, state, index, tool_call_count
                )
                tool_call_count += len(results)
                conversation.append(Message(role="user", tool_results=results))
            else:
                state.notes.setdefault("tool_loops_exhausted", []).append(index)

            await buffer.flush()
        except RefusalError as exc:
            await buffer.flush()
            await self._emitter.emit(
                ev.STEP_END,
                step=index,
                agent_name=agent.name,
                refused=True,
                category=exc.category,
            )
            raise

        latency_ms = int((time.perf_counter() - started) * 1000)
        output = "\n\n".join(part for part in text_parts if part.strip())
        state.append(agent, output, visible_to=private_to)
        await self._capture_artifacts(state, agent, output, index)

        record = StepRecord(
            index=index,
            agent=agent,
            instruction=instruction,
            output=output,
            provider=provider,
            tokens_in=totals["tokens_in"],
            tokens_out=totals["tokens_out"],
            cached_tokens=totals["cached"],
            reasoning_tokens=totals["reasoning"],
            cost_microcents=totals["cost"],
            billable_microcents=totals["billable"],
            latency_ms=latency_ms,
            attempts=attempts,
            stop_reason=stop_reason,
            model_calls=model_calls,
            tool_calls=tool_call_count,
        )
        if self._step_sink is not None:
            await self._step_sink(record)

        await self._emitter.emit(
            ev.STEP_END,
            step=index,
            agent_id=agent.id,
            agent_name=agent.name,
            role=agent.role,
            model=agent.model,
            provider=provider,
            tokens_in=totals["tokens_in"],
            tokens_out=totals["tokens_out"],
            cached_tokens=totals["cached"],
            cost_microcents=totals["cost"],
            latency_ms=latency_ms,
            attempts=attempts,
            model_calls=model_calls,
            tool_calls=tool_call_count,
            spent_microcents=self._budget.spent_microcents,
            steps_used=self._budget.steps_used,
            output=output,
        )

        if self._budget.take_warning():
            await self._emitter.emit(
                ev.BUDGET_WARNING,
                spent_microcents=self._budget.spent_microcents,
                limit_microcents=self._budget.max_cost_microcents,
            )

        return output

    async def _complete(
        self,
        *,
        agent: AgentSpec,
        system: str,
        conversation: list[Message],
        state: RunState,
        instruction: str,
        json_mode: bool,
        tools: list[ToolSpec],
        buffer: TokenBuffer,
        first_call: bool,
    ):
        try:
            return await self._llm.complete(
                model=agent.model,
                system=system,
                messages=conversation,
                max_tokens=agent.max_tokens,
                effort=agent.effort,
                json_mode=json_mode,
                tools=tools,
                timeout_seconds=self._step_timeout,
                on_token=buffer.push if self._stream_tokens else None,
            )
        except ContextWindowExceeded:
            # Retry once against a hard-compacted board rather than failing the
            # whole run: the recent turns are usually enough to continue. Only
            # the opening message can be rebuilt — a tool exchange mid-turn has
            # to stay intact or the call ids stop matching.
            await buffer.flush()
            log.warning("engine.context_overflow", agent=agent.name, first_call=first_call)
            if not first_call:
                raise
            compacted = [
                Message(
                    role="user",
                    content=self._user_message(agent, state, instruction, max_chars=40_000),
                )
            ]
            return await self._llm.complete(
                model=agent.model,
                system=system,
                messages=compacted,
                max_tokens=agent.max_tokens,
                effort=agent.effort,
                json_mode=json_mode,
                tools=tools,
                timeout_seconds=self._step_timeout,
                on_token=None,
            )

    async def _run_tools(
        self,
        calls: list[ToolCallRequest],
        tool_by_name: dict[str, Tool],
        agent: AgentSpec,
        state: RunState,
        step_index: int,
        offset: int,
    ) -> list[ToolResultPart]:
        """Execute the tools a model asked for.

        Read-only tools run concurrently; anything that writes runs in the order
        the model requested it, because two edits to the same artifact must not
        interleave.
        """
        context = ToolContext(
            run_id=self._emitter.run_id,
            org_id=state.notes.get("org_id", ""),
            agent_id=agent.id,
            agent_name=agent.name,
            step_index=step_index,
            artifacts=state.artifacts,
            read_artifact_version=state.artifact_versions,
            write_artifact=self._make_writer(state, agent, step_index),
        )

        async def execute(position: int, call: ToolCallRequest) -> ToolResultPart:
            tool = tool_by_name.get(call.name)
            await self._emitter.emit(
                ev.TOOL_CALL,
                step=step_index,
                call=offset + position,
                tool=call.name,
                agent_name=agent.name,
                arguments=_safe_arguments(call.arguments),
            )

            started = time.perf_counter()
            if tool is None:
                # A hallucinated tool name: tell the model what it may use
                # rather than failing the run.
                available = ", ".join(sorted(tool_by_name)) or "(none)"
                result = ToolResult(
                    content=f"Error: no tool named {call.name!r}. Available tools: {available}",
                    is_error=True,
                )
            else:
                result = await tool.invoke(call.arguments, context)
            latency_ms = int((time.perf_counter() - started) * 1000)

            await self._emitter.emit(
                ev.TOOL_RESULT,
                step=step_index,
                call=offset + position,
                tool=call.name,
                agent_name=agent.name,
                is_error=result.is_error,
                latency_ms=latency_ms,
                preview=result.content[:400],
                metadata=result.metadata,
            )

            if self._tool_call_sink is not None:
                await self._tool_call_sink(
                    ToolCallRecord(
                        step_index=step_index,
                        call_index=offset + position,
                        agent_id=agent.id,
                        agent_name=agent.name,
                        tool=call.name,
                        arguments=_safe_arguments(call.arguments),
                        result=result.content,
                        is_error=result.is_error,
                        latency_ms=latency_ms,
                        metadata=result.metadata,
                    )
                )

            return ToolResultPart(
                tool_call_id=call.id,
                name=call.name,
                content=result.content,
                is_error=result.is_error,
            )

        mutating = any(
            (tool_by_name.get(call.name) or _UNKNOWN).side_effect is SideEffect.WRITE
            for call in calls
        )
        if mutating:
            return [await execute(position, call) for position, call in enumerate(calls)]
        return list(
            await asyncio.gather(
                *(execute(position, call) for position, call in enumerate(calls))
            )
        )

    def _make_writer(self, state: RunState, agent: AgentSpec, step_index: int):
        async def write(path: str, content: str, kind: str) -> int:
            version = state.record_artifact(path, content, kind, agent.name)
            if self._artifact_sink is not None:
                await self._artifact_sink(
                    ArtifactRecord(
                        path=path,
                        version=version,
                        kind=kind,
                        language=None,
                        content=content,
                        step_index=step_index,
                        agent_name=agent.name,
                    )
                )
            await self._emitter.emit(
                ev.ARTIFACT_WRITTEN,
                step=step_index,
                path=path,
                version=version,
                kind=kind,
                size_bytes=len(content.encode()),
                agent_name=agent.name,
                via="tool",
            )
            return version

        return write

    async def call_agent_json(
        self,
        agent: AgentSpec,
        state: RunState,
        instruction: str,
        *,
        required_keys: tuple[str, ...] = (),
        retries: int = 1,
    ) -> dict[str, Any]:
        """Ask an agent for a JSON object.

        Parsing is lenient and, on failure, the agent is shown its own malformed
        output and asked again. Native structured-output modes differ across the
        four providers, so a uniform reprompt keeps every topology portable.
        """
        prompt = instruction
        last_error = ""
        for attempt in range(retries + 1):
            raw = await self.call_agent(agent, state, prompt, json_mode=True)
            try:
                parsed = extract_json(raw)
                missing = [key for key in required_keys if key not in parsed]
                if missing:
                    raise ValueError(f"missing required key(s): {', '.join(missing)}")
                return parsed
            except ValueError as exc:
                last_error = str(exc)
                if attempt == retries:
                    break
                prompt = (
                    f"{instruction}\n\n"
                    f"Your previous reply could not be parsed: {last_error}. "
                    f"Reply with a single valid JSON object and nothing else — "
                    f"no prose, no code fence."
                )
        raise ValueError(f"agent {agent.name} did not return usable JSON: {last_error}")

    async def _capture_artifacts(
        self, state: RunState, agent: AgentSpec, text: str, step_index: int
    ) -> None:
        for artifact in extract(text):
            version = state.record_artifact(
                artifact.path, artifact.content, artifact.kind, agent.name
            )
            record = ArtifactRecord(
                path=artifact.path,
                version=version,
                kind=artifact.kind,
                language=artifact.language,
                content=artifact.content,
                step_index=step_index,
                agent_name=agent.name,
            )
            if self._artifact_sink is not None:
                await self._artifact_sink(record)
            await self._emitter.emit(
                ev.ARTIFACT_WRITTEN,
                step=step_index,
                path=artifact.path,
                version=version,
                kind=artifact.kind,
                size_bytes=artifact.size_bytes,
                agent_name=agent.name,
                via="message",
            )

    def _system_prompt(self, agent: AgentSpec, tools: list[Tool]) -> str:
        header = (
            f"You are {agent.name}, the {agent.role} on a team of AI agents working "
            f"one shared task.\n"
            "Everything you write is visible to the other agents, so make your output "
            "self-contained and state assumptions explicitly. Do the work you were "
            "asked for — do not restate the task or narrate your process."
        )
        parts = [header]
        if agent.system_prompt.strip():
            parts.append(agent.system_prompt.strip())

        if tools:
            parts.append(_tool_guidance(tools))
        # With write tools available, emitting a whole file into the message as
        # well would store it twice under two different versions.
        if not any(tool.side_effect is SideEffect.WRITE for tool in tools):
            parts.append(ARTIFACT_INSTRUCTIONS)
        return "\n\n".join(parts)

    def _user_message(
        self,
        agent: AgentSpec,
        state: RunState,
        instruction: str,
        *,
        max_chars: int | None = None,
    ) -> str:
        transcript = state.transcript(
            agent_id=agent.id, max_chars=max_chars or self._max_board_chars
        )
        return (
            f"## Original request\n{state.user_input}\n\n"
            f"## Shared board\n{transcript}\n\n"
            f"## Artifacts so far\n{state.artifact_index()}\n\n"
            f"## Your task now\n{instruction}"
        )


def _default_tool_resolver(agent: AgentSpec) -> list[Tool]:
    from core.tools import resolve

    names = [name for name in (agent.tools or []) if isinstance(name, str)]
    return resolve(names)


class _UnknownTool:
    side_effect = SideEffect.READ_ONLY


_UNKNOWN = _UnknownTool()


def _tool_guidance(tools: list[Tool]) -> str:
    has_write = any(tool.side_effect is SideEffect.WRITE for tool in tools)
    lines = [
        "You have tools. Call them when they would give you something you do not "
        "already have; do not narrate what you are about to do before calling one.",
    ]
    if has_write:
        lines.append(
            "Write files with the artifact tools rather than pasting them into your "
            "reply — a file written by a tool is versioned and readable by the other "
            "agents. Read a file before editing it, and prefer `edit_artifact` over "
            "rewriting a whole file."
        )
    lines.append(
        "When you have what you need, stop calling tools and give your answer."
    )
    return "\n".join(lines)


# Argument values are echoed into events and the audit trail; a model can be
# talked into putting a credential in one, so anything that looks like a secret
# is masked on the way out.
_SECRETISH = re.compile(
    r"\b(sk-[A-Za-z0-9_\-]{12,}|xai-[A-Za-z0-9_\-]{8,}|AIza[A-Za-z0-9_\-]{16,})"
)


def _safe_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in arguments.items():
        if isinstance(value, str):
            trimmed = value[:2000]
            cleaned[key] = _SECRETISH.sub("[redacted]", trimmed)
        else:
            cleaned[key] = value
    return cleaned


_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """Pull a JSON object out of a model reply.

    Tries the fenced block first, then the outermost brace pair, then the raw
    string. Models wrap JSON in prose often enough that a bare `json.loads` on
    the reply fails constantly in production.
    """
    candidates: list[str] = []
    fenced = _JSON_FENCE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        candidates.append(text[first : last + 1])
    candidates.append(text.strip())

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"expected a JSON object, got: {text[:200]!r}")
