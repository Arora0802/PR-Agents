from pathlib import Path

import pytest

from pr_review_agent import review_agent
from pr_review_agent.review_agent import PRReviewAgent


def test_build_convention_profile_reads_config_files(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff]\nline-length = 100\n", encoding="utf-8"
    )
    (tmp_path / ".editorconfig").write_text(
        "[*.py]\nindent_style = space\nindent_size = 4\n", encoding="utf-8"
    )

    agent = PRReviewAgent(tmp_path)
    profile = agent.build_convention_profile()

    assert profile["config_files"] == [".editorconfig", "pyproject.toml"]
    assert profile["style"][0]["name"] == "line_length"
    assert profile["style"][0]["value"] == 100


def test_ingest_diff_parses_hunks():
    diff = """diff --git a/app.py b/app.py
index 1111111..2222222 100644
--- a/app.py
+++ b/app.py
@@ -1,2 +1,3 @@
-old line
+new line
+extra line
"""

    agent = PRReviewAgent(".")
    hunks = agent.ingest_diff(diff)

    assert len(hunks) == 1
    assert hunks[0]["file"] == "app.py"
    assert hunks[0]["new_start"] == 1
    assert hunks[0]["new_count"] == 3


def test_review_diff_returns_structured_comments():
    diff = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1,2 @@
-old
+new
+extra
"""

    agent = PRReviewAgent(".")
    comments = agent.review_diff(diff)

    assert comments[0]["file"] == "app.py"
    assert comments[0]["severity"] in {"blocking", "suggestion", "nitpick"}
    assert "comment" in comments[0]


def test_load_repo_context_reads_github_metadata(monkeypatch):
    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return self._payload.encode("utf-8")

    def fake_urlopen(request, timeout=10):
        return FakeResponse('{"full_name": "octo/demo", "default_branch": "main", "language": "Python"}')

    monkeypatch.setattr(review_agent, "urlopen", fake_urlopen)

    agent = PRReviewAgent(".")
    context = agent.load_repo_context("octo/demo")

    assert context["full_name"] == "octo/demo"
    assert context["default_branch"] == "main"


def test_review_diff_can_use_llm_override():
    class FakeLLMClient:
        def complete(self, prompt: str) -> str:
            return '{"severity": "blocking", "category": "correctness", "comment": "LLM flagged this", "suggested_fix": "Use a guard clause"}'

    diff = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1,2 @@
-old
+new
"""

    agent = PRReviewAgent(".")
    comments = agent.review_diff(diff, llm_client=FakeLLMClient())

    assert comments[0]["comment"] == "LLM flagged this"
    assert comments[0]["severity"] == "blocking"


def test_post_comments_to_pr_uses_github_api(monkeypatch):
    calls = {}

    class FakeResponse:
        status = 201

        def read(self):
            return b"{}"

    def fake_urlopen(request, timeout=10):
        calls["url"] = request.full_url
        calls["method"] = request.get_method()
        calls["data"] = request.data.decode("utf-8") if request.data else None
        return FakeResponse()

    monkeypatch.setattr(review_agent, "urlopen", fake_urlopen)

    agent = PRReviewAgent(".")
    result = agent.post_comments_to_pr("octo/demo", [{"body": "hello"}], "token", 7)

    assert result["status"] == 201
    assert calls["url"].endswith("/repos/octo/demo/issues/7/comments")
    assert "hello" in calls["data"]

