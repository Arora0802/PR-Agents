# PR Review Agent

This workspace contains a lightweight Python agent that profiles a repository's conventions and reviews a PR diff against them.

## Features

- Detects simple repo conventions from `.editorconfig`, `pyproject.toml`, and `package.json`
- Parses unified diff hunks into structured review targets
- Produces structured review comments with severity and suggested fixes
- Renders findings as Markdown for PR-style reporting

## Run the tests

```bash
python -m pytest -q
```

## Run the CLI

```bash
python -m pr_review_agent.cli . sample.diff
```

You can also pipe a diff into stdin instead of providing a file.

## Optional extensions

- LLM review override: pass an object with a `complete(prompt)` method to `review_diff(..., llm_client=...)`
- GitHub repo context: call `load_repo_context("owner/repo")`
- GitHub PR comments: call `post_comments_to_pr("owner/repo", comments, "<token>", 123)`
