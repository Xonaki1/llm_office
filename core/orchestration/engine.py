from __future__ import annotations

import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog

from core import events as ev
from core.artifacts import ARTIFACT_INSTRUCTIONS, extract
from core.events import EventEmitter, TokenBuffer
from core.llm.providers import ContextWindowExceeded, Message, RefusalError
from core.llm.router import LLMRouter
from core.orchestration.budget import BudgetGuard
from core.orchestration.state import AgentSpec, RunState

log = structlog.get_logger(__name__)


@dataclass
class StepRecord:
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


class Engine:
    """Executes individual agent turns.

    Topologies (see presets.py) decide *who* speaks next; the engine owns
    budget enforcement, cancellation, streaming, artifact capture, event
    emission and persistence. Splitting it this way means a new topology is one
    function and needs no knowledge of any of that.
    """

    def __init__(
        self,
        *,
        llm: LLMRouter,
        emitter: EventEmitter,
        budget: BudgetGuard,
        step_sink: StepSink | None = None,
        artifact_sink: ArtifactSink | None = None,
        markup: Callable[[int], int] | None = None,
        max_board_chars: int = 400_000,
        step_timeout_seconds: float = 600.0,
        stream_tokens: bool = True,
    ) -> None:
        self._llm = llm
        self._emitter = emitter
        self._budget = budget
        self._step_sink = step_sink
        self._artifact_sink = artifact_sink
        self._markup = markup or (lambda value: value)
        self._max_board_chars = max_board_chars
        self._step_timeout = step_timeout_seconds
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
    ) -> str:
        await self._budget.check_before_step()
        state.step_index += 1
        index = state.step_index

        await self._emitter.emit(
            ev.STEP_START,
            step=index,
            agent_id=agent.id,
            agent_name=agent.name,
            role=agent.role,
            model=agent.model,
            effort=agent.effort,
            instruction=instruction,
        )

        system = self._system_prompt(agent)
        user_content = self._user_message(agent, state, instruction)

        buffer = TokenBuffer(self._emitter, index)
        started = time.perf_counter()
        try:
            routed = await self._llm.complete(
                model=agent.model,
                system=system,
                messages=[Message(role="user", content=user_content)],
                max_tokens=agent.max_tokens,
                effort=agent.effort,
                json_mode=json_mode,
                timeout_seconds=self._step_timeout,
                on_token=buffer.push if self._stream_tokens else None,
            )
            await buffer.flush()
        except ContextWindowExceeded:
            # Retry once against a hard-compacted board rather than failing the
            # whole run: the recent turns are usually enough to continue.
            await buffer.flush()
            log.warning("engine.context_overflow", step=index, agent=agent.name)
            compacted = self._user_message(agent, state, instruction, max_chars=40_000)
            routed = await self._llm.complete(
                model=agent.model,
                system=system,
                messages=[Message(role="user", content=compacted)],
                max_tokens=agent.max_tokens,
                effort=agent.effort,
                json_mode=json_mode,
                timeout_seconds=self._step_timeout,
                on_token=None,
            )
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
        result = routed.result
        billable = self._markup(routed.cost_microcents) if routed.billed_to_platform else 0
        self._budget.record(routed.cost_microcents)
        state.append(agent, result.text, visible_to=private_to)

        await self._capture_artifacts(state, agent, result.text, index)

        record = StepRecord(
            index=index,
            agent=agent,
            instruction=instruction,
            output=result.text,
            provider=result.provider,
            tokens_in=result.usage.input_tokens,
            tokens_out=result.usage.output_tokens,
            cached_tokens=result.usage.cached_input_tokens,
            reasoning_tokens=result.usage.reasoning_tokens,
            cost_microcents=routed.cost_microcents,
            billable_microcents=billable,
            latency_ms=latency_ms,
            attempts=routed.attempts,
            stop_reason=result.stop_reason,
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
            provider=result.provider,
            tokens_in=result.usage.input_tokens,
            tokens_out=result.usage.output_tokens,
            cached_tokens=result.usage.cached_input_tokens,
            cost_microcents=routed.cost_microcents,
            latency_ms=latency_ms,
            attempts=routed.attempts,
            spent_microcents=self._budget.spent_microcents,
            steps_used=self._budget.steps_used,
            output=result.text,
        )

        if self._budget.take_warning():
            await self._emitter.emit(
                ev.BUDGET_WARNING,
                spent_microcents=self._budget.spent_microcents,
                limit_microcents=self._budget.max_cost_microcents,
            )

        return result.text

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
            )

    def _system_prompt(self, agent: AgentSpec) -> str:
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
