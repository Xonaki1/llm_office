from __future__ import annotations

import pytest

from core.tools import SideEffect, ToolContext, catalogue, known_names, resolve
from core.tools.artifacts import EditArtifact, ListArtifacts, ReadArtifact, WriteArtifact
from core.tools.web import WebFetch, assert_safe_url, html_to_text


@pytest.fixture
def context():
    written: list[tuple[str, str, str]] = []
    artifacts = {"src/app.py": "line one\nline two\nline three"}
    versions = {"src/app.py": 1}

    async def write(path: str, content: str, kind: str) -> int:
        written.append((path, content, kind))
        artifacts[path] = content
        versions[path] = versions.get(path, 0) + 1
        return versions[path]

    ctx = ToolContext(
        run_id="run-1",
        org_id="org-1",
        agent_id="agent-1",
        agent_name="Ann",
        step_index=1,
        artifacts=artifacts,
        read_artifact_version=versions,
        write_artifact=write,
    )
    ctx.written = written  # type: ignore[attr-defined]
    return ctx


class TestRegistry:
    def test_every_tool_is_uniquely_named(self):
        names = [info.name for info in catalogue()]
        assert len(names) == len(set(names))

    def test_resolve_drops_unknown_names(self):
        resolved = resolve(["read_artifact", "not_a_tool", "write_artifact"])
        assert [t.name for t in resolved] == ["read_artifact", "write_artifact"]

    def test_web_search_is_hidden_without_a_provider(self):
        # An unusable tool in the schema is worse than a missing one: the model
        # keeps trying it and burns steps on the failure.
        info = next(i for i in catalogue() if i.name == "web_search")
        assert info.available is False
        assert "search provider" in (info.unavailable_reason or "")
        assert "web_search" not in [t.name for t in resolve(["web_search"])]

    def test_every_schema_is_a_json_object(self):
        for name in known_names():
            from core.tools import get

            tool = get(name)
            assert tool is not None
            assert tool.parameters["type"] == "object"
            assert "properties" in tool.parameters


class TestArtifactTools:
    async def test_list(self, context):
        result = await ListArtifacts().invoke({}, context)
        assert "src/app.py" in result.content
        assert result.is_error is False

    async def test_list_with_prefix_that_matches_nothing(self, context):
        result = await ListArtifacts().invoke({"prefix": "docs/"}, context)
        assert "No artifacts under" in result.content

    async def test_read_whole_file(self, context):
        result = await ReadArtifact().invoke({"path": "src/app.py"}, context)
        assert result.content == "line one\nline two\nline three"

    async def test_read_line_range_is_numbered(self, context):
        result = await ReadArtifact().invoke(
            {"path": "src/app.py", "start_line": 2, "line_count": 1}, context
        )
        assert result.content == "2\tline two"

    async def test_read_missing_file_lists_what_exists(self, context):
        result = await ReadArtifact().invoke({"path": "nope.py"}, context)
        assert result.is_error
        assert "src/app.py" in result.content

    async def test_write_creates_a_new_version(self, context):
        result = await WriteArtifact().invoke(
            {"path": "src/new.py", "content": "x = 1"}, context
        )
        assert result.is_error is False
        assert result.metadata["version"] == 1
        assert context.written[-1][0] == "src/new.py"

    async def test_write_rejects_traversal(self, context):
        result = await WriteArtifact().invoke(
            {"path": "../../etc/passwd", "content": "bad"}, context
        )
        assert result.is_error
        assert "traversal" in result.content

    async def test_write_rejects_an_absolute_path(self, context):
        result = await WriteArtifact().invoke({"path": "/etc/hosts", "content": "x"}, context)
        assert result.is_error

    async def test_write_rejects_an_oversized_file(self, context):
        result = await WriteArtifact().invoke(
            {"path": "big.txt", "content": "x" * 500_000}, context
        )
        assert result.is_error
        assert "limit" in result.content

    async def test_edit_replaces_a_unique_substring(self, context):
        result = await EditArtifact().invoke(
            {"path": "src/app.py", "old_text": "line two", "new_text": "LINE TWO"}, context
        )
        assert result.is_error is False
        assert context.artifacts["src/app.py"] == "line one\nLINE TWO\nline three"

    async def test_edit_refuses_an_ambiguous_match(self, context):
        context.artifacts["dup.py"] = "a = 1\na = 1"
        result = await EditArtifact().invoke(
            {"path": "dup.py", "old_text": "a = 1", "new_text": "a = 2"}, context
        )
        assert result.is_error
        assert "unique" in result.content

    async def test_edit_reports_a_missing_match_usefully(self, context):
        result = await EditArtifact().invoke(
            {"path": "src/app.py", "old_text": "not present", "new_text": "x"}, context
        )
        assert result.is_error
        assert "does not appear" in result.content

    async def test_missing_required_argument_is_an_error_not_a_crash(self, context):
        result = await WriteArtifact().invoke({"path": "a.py"}, context)
        assert result.is_error
        assert "content" in result.content

    async def test_wrong_argument_type_is_an_error(self, context):
        result = await ReadArtifact().invoke({"path": 12345}, context)
        assert result.is_error

    async def test_side_effects_are_declared(self):
        assert ReadArtifact().side_effect is SideEffect.READ_ONLY
        assert WriteArtifact().side_effect is SideEffect.WRITE
        assert WebFetch().side_effect is SideEffect.NETWORK


class TestResultLimits:
    async def test_a_huge_result_is_truncated(self, context):
        context.artifacts["huge.txt"] = "y" * 200_000
        result = await ReadArtifact().invoke({"path": "huge.txt"}, context)
        # The result is fed straight back into the next prompt and billed as
        # input tokens, so it must be bounded.
        assert len(result.content) < 100_000
        assert "omitted" in result.content

    async def test_a_tool_that_hangs_is_cut_off(self, context):
        import asyncio

        from core.tools.base import Tool, ToolResult

        class Hanging(Tool):
            name = "hang"
            description = "hangs"
            parameters = {"type": "object", "properties": {}}
            timeout_seconds = 0.05

            async def run(self, arguments, ctx):
                await asyncio.sleep(5)
                return ToolResult(content="never")

        result = await Hanging().invoke({}, context)
        assert result.is_error
        assert "timed out" in result.content

    async def test_an_exploding_tool_does_not_kill_the_run(self, context):
        from core.tools.base import Tool

        class Exploding(Tool):
            name = "boom"
            description = "raises"
            parameters = {"type": "object", "properties": {}}

            async def run(self, arguments, ctx):
                raise RuntimeError("internal detail that must not leak")

        result = await Exploding().invoke({}, context)
        assert result.is_error
        assert "internal detail" not in result.content
        assert result.metadata["exception"] == "RuntimeError"


class TestSsrfGuard:
    """A model can be talked into fetching a URL by anything it reads, so the
    guard is the security boundary for the whole network tool surface."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8000/admin",
            "http://localhost/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/",
            "http://0.0.0.0/",
            "http://10.0.0.1/internal",
            "http://192.168.1.1/",
            "http://172.16.0.5/",
            "http://metadata.google.internal/",
        ],
    )
    async def test_private_and_metadata_targets_are_refused(self, url):
        from core.tools.web import UnsafeUrl

        with pytest.raises(UnsafeUrl):
            await assert_safe_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://example.com/x",
            "gopher://example.com/",
            "data:text/html,<script>",
        ],
    )
    async def test_non_http_schemes_are_refused(self, url):
        from core.tools.web import UnsafeUrl

        with pytest.raises(UnsafeUrl):
            await assert_safe_url(url)

    async def test_unresolvable_host_is_refused(self):
        from core.tools.web import UnsafeUrl

        with pytest.raises(UnsafeUrl):
            await assert_safe_url("http://this-host-does-not-exist.invalid/")

    async def test_deny_list_is_honoured(self, monkeypatch):
        from core.config import get_settings
        from core.tools.web import UnsafeUrl

        settings = get_settings()
        monkeypatch.setattr(settings, "tool_blocked_hosts", ["example.com"])
        with pytest.raises(UnsafeUrl, match="deny list"):
            await assert_safe_url("http://sub.example.com/page")

    async def test_allow_list_excludes_everything_else(self, monkeypatch):
        from core.config import get_settings
        from core.tools.web import UnsafeUrl

        settings = get_settings()
        monkeypatch.setattr(settings, "tool_allowed_hosts", ["docs.internal.test"])
        with pytest.raises(UnsafeUrl, match="allow list"):
            await assert_safe_url("http://example.com/")

    async def test_error_does_not_echo_the_resolved_address(self, monkeypatch):
        """On a blind SSRF probe the error text is the oracle the attacker
        wants: it must not reveal what an innocuous-looking hostname resolved
        to, only that the target was refused."""
        import core.tools.web as web

        async def fake_resolve(host: str) -> list[str]:
            return ["10.11.12.13"]

        monkeypatch.setattr(web, "_resolve", fake_resolve)

        with pytest.raises(web.UnsafeUrl) as excinfo:
            await assert_safe_url("http://intranet.example.com/")
        assert "10.11.12.13" not in str(excinfo.value)
        assert "non-public" in str(excinfo.value)


class TestHtmlExtraction:
    def test_scripts_and_styles_are_dropped(self):
        text, title = html_to_text(
            "<html><head><title>Doc</title><style>b{}</style></head>"
            "<body><script>alert(1)</script><p>Hello</p><p>World</p></body></html>"
        )
        assert title == "Doc"
        assert "alert" not in text
        assert "Hello" in text and "World" in text

    def test_block_elements_become_line_breaks(self):
        text, _ = html_to_text("<div>one</div><div>two</div>")
        assert "one" in text and "two" in text
        assert "onetwo" not in text.replace(" ", "").replace("\n", "x")

    def test_entities_are_decoded(self):
        text, _ = html_to_text("<p>a &amp; b &lt;c&gt;</p>")
        assert "a & b <c>" in text
