from __future__ import annotations

import pytest

from core.artifacts import UnsafeArtifactPath, extract, sanitise_path


class TestPathSafety:
    """Artifact paths come from model output, which is untrusted input."""

    @pytest.mark.parametrize(
        "path",
        [
            "/etc/passwd",
            "C:/Windows/system32/config",
            "../../../etc/shadow",
            "src/../../secrets.env",
            "..",
            "",
            "   ",
        ],
    )
    def test_dangerous_paths_are_rejected(self, path):
        with pytest.raises(UnsafeArtifactPath):
            sanitise_path(path)

    def test_backslashes_are_normalised(self):
        assert sanitise_path("src\\app\\main.py") == "src/app/main.py"

    def test_redundant_segments_are_collapsed(self):
        assert sanitise_path("./src//app/./main.py") == "src/app/main.py"

    def test_long_paths_are_rejected(self):
        with pytest.raises(UnsafeArtifactPath):
            sanitise_path("a/" * 300)


class TestExtraction:
    def test_fenced_block_with_a_path(self):
        text = """Here is the module.

```python path=src/app.py
print("hello")
```

That should do it."""
        artifacts = extract(text)
        assert len(artifacts) == 1
        assert artifacts[0].path == "src/app.py"
        assert artifacts[0].language == "python"
        assert artifacts[0].kind == "code"
        assert artifacts[0].content == 'print("hello")'

    def test_unlabelled_blocks_are_ignored(self):
        text = """Example:

```python
print("this is illustrative")
```
"""
        assert extract(text) == []

    def test_artifact_tag(self):
        text = """<artifact path="docs/spec.md" kind="markdown">
# Spec
Body text.
</artifact>"""
        artifacts = extract(text)
        assert len(artifacts) == 1
        assert artifacts[0].path == "docs/spec.md"
        assert artifacts[0].kind == "markdown"
        assert "# Spec" in artifacts[0].content

    def test_multiple_artifacts_in_one_message(self):
        text = """```python path=a.py
a = 1
```

```json path=config/settings.json
{"x": 1}
```"""
        paths = sorted(a.path for a in extract(text))
        assert paths == ["a.py", "config/settings.json"]

    def test_last_definition_of_a_path_wins(self):
        text = """```python path=a.py
first = 1
```

Actually, corrected:

```python path=a.py
second = 2
```"""
        artifacts = extract(text)
        assert len(artifacts) == 1
        assert "second" in artifacts[0].content

    def test_traversal_in_a_block_is_skipped_not_fatal(self):
        text = """```python path=../../evil.py
bad = True
```

```python path=ok.py
good = True
```"""
        artifacts = extract(text)
        assert [a.path for a in artifacts] == ["ok.py"]

    def test_kind_falls_back_to_the_extension(self):
        text = """<artifact path="notes/readme.md">
text
</artifact>"""
        assert extract(text)[0].kind == "markdown"

    def test_prose_only_message_yields_nothing(self):
        assert extract("Just an explanation with no files in it.") == []
