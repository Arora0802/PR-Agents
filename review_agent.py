from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib


class PRReviewAgent:
    def __init__(self, repo_path: str | Path):
        self.repo_path = Path(repo_path)

    def load_repo_context(self, repo_name: str) -> Dict[str, Any]:
        url = f"https://api.github.com/repos/{repo_name}"
        request = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "pr-review-agent"})
        response = urlopen(request, timeout=10)
        if hasattr(response, "__enter__"):
            with response as handled_response:
                payload = json.loads(handled_response.read().decode("utf-8"))
        else:
            payload = json.loads(response.read().decode("utf-8"))

        return {
            "full_name": payload.get("full_name", repo_name),
            "default_branch": payload.get("default_branch", "main"),
            "language": payload.get("language", "Unknown"),
            "description": payload.get("description", ""),
        }

    def post_comments_to_pr(self, repo_name: str, comments: List[Dict[str, Any]], token: str, pr_number: int) -> Dict[str, Any]:
        url = f"https://api.github.com/repos/{repo_name}/issues/{pr_number}/comments"
        payload = json.dumps([{"body": c.get("body") or c.get("comment", "")} for c in comments]).encode("utf-8")
        request = Request(
            url,
            data=payload,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "pr-review-agent",
            },
            method="POST",
        )
        response = urlopen(request, timeout=10)
        if hasattr(response, "__enter__"):
            with response as handled_response:
                return {"status": getattr(handled_response, "status", 201), "body": handled_response.read().decode("utf-8")}

        return {"status": getattr(response, "status", 201), "body": response.read().decode("utf-8")}

    def _llm_review_override(self, llm_client: Any, hunk: Dict[str, Any], profile: Dict[str, Any], changed_lines: str) -> Optional[Dict[str, Any]]:
        if llm_client is None:
            return None

        prompt = (
            "You are reviewing a diff against repository conventions. "
            f"Repo profile: {json.dumps(profile)}\n"
            f"Changed hunk: {json.dumps(hunk)}\n"
            f"Changed lines: {changed_lines}\n"
            "Return JSON with severity, category, comment, suggested_fix."
        )
        try:
            raw = llm_client.complete(prompt)
        except Exception:
            return None

        try:
            parsed = json.loads(raw)
        except Exception:
            return None

        return {
            "severity": parsed.get("severity", "suggestion"),
            "category": parsed.get("category", "convention"),
            "comment": parsed.get("comment", ""),
            "suggested_fix": parsed.get("suggested_fix", ""),
        }

    def build_convention_profile(self) -> Dict[str, Any]:
        config_files: List[str] = []
        style: List[Dict[str, Any]] = []
        test_patterns: List[str] = []

        if self.repo_path.exists():
            for candidate in [".editorconfig", "pyproject.toml", "package.json", ".eslintrc", ".pylintrc"]:
                if (self.repo_path / candidate).exists():
                    config_files.append(candidate)

        line_length: Any = None
        if (self.repo_path / "pyproject.toml").exists():
            pyproject_text = (self.repo_path / "pyproject.toml").read_text(encoding="utf-8")
            try:
                pyproject = tomllib.loads(pyproject_text)
            except Exception:
                pyproject = {}
            tool_settings = pyproject.get("tool", {})
            ruff_settings = tool_settings.get("ruff", {})
            line_length = ruff_settings.get("line-length") or tool_settings.get("black", {}).get("line-length")

        if line_length is not None:
            style.append({"name": "line_length", "value": line_length})

        if (self.repo_path / ".editorconfig").exists():
            editorconfig = (self.repo_path / ".editorconfig").read_text(encoding="utf-8")
            if "indent_style = space" in editorconfig:
                style.append({"name": "indent_style", "value": "space"})
            if "indent_size = 4" in editorconfig:
                style.append({"name": "indent_size", "value": 4})

        if (self.repo_path / "package.json").exists():
            package_json = (self.repo_path / "package.json").read_text(encoding="utf-8")
            if "pytest" in package_json or "jest" in package_json:
                test_patterns.append("jest/pytest")

        if (self.repo_path / "tests").exists():
            test_patterns.append("tests_directory")

        naming = "snake_case"
        if self.repo_path.exists():
            python_files = [p for p in self.repo_path.rglob("*.py") if p.is_file()]
            if python_files:
                sample_text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in python_files[:3])
                if re.search(r"\b[a-z]+[A-Z][A-Za-z0-9_]*\b", sample_text):
                    naming = "camelCase"

        return {
            "naming": naming,
            "style": style,
            "test_patterns": test_patterns,
            "config_files": config_files,
        }

    def ingest_diff(self, diff_text: str) -> List[Dict[str, Any]]:
        hunks: List[Dict[str, Any]] = []
        current_file: str | None = None
        current_hunk: Dict[str, Any] | None = None

        for raw_line in diff_text.splitlines():
            line = raw_line.rstrip("\n")
            if line.startswith("diff --git "):
                match = re.match(r"diff --git a/(.+) b/(.+)", line)
                if match:
                    current_file = match.group(2)
                continue

            if line.startswith("--- ") and current_file is None:
                current_file = line[4:].split("\t", 1)[0]
                continue

            if line.startswith("+++ "):
                continue

            hunk_match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            if hunk_match:
                if current_hunk is not None:
                    hunks.append(current_hunk)
                current_hunk = {
                    "file": current_file or "unknown",
                    "new_start": int(hunk_match.group(1)),
                    "new_count": int(hunk_match.group(2) or "1"),
                    "changes": [],
                }
                continue

            if current_hunk is not None and line.startswith(("+", "-")):
                current_hunk["changes"].append(line)

        if current_hunk is not None:
            hunks.append(current_hunk)

        return hunks

    def review_diff(self, diff_text: str, llm_client: Any = None) -> List[Dict[str, Any]]:
        profile = self.build_convention_profile()
        hunks = self.ingest_diff(diff_text)
        comments: List[Dict[str, Any]] = []

        for hunk in hunks:
            changed_lines = "\n".join(hunk.get("changes", []))
            line = hunk.get("new_start", 1)
            severity = "suggestion"
            category = "convention"
            comment = (
                f"This change should stay aligned with the repository's {profile['naming']} conventions "
                f"and any local style settings from {', '.join(profile['config_files']) or 'the repo'}"
            )
            suggested_fix = "Keep naming and indentation consistent with the surrounding code."

            if re.search(r"\beval\(|\bexec\(|os\.system\(|subprocess\.call\(", changed_lines):
                severity = "blocking"
                category = "security"
                comment = "Dynamic code execution is risky and should be avoided in this diff."
                suggested_fix = "Replace dynamic execution with a safer explicit API or validated input handling."
            elif re.search(r"except:\s*$|except\s+Exception\s*:\s*$", changed_lines):
                severity = "suggestion"
                category = "correctness"
                comment = "The error handling here is too broad; it can hide important failures."
                suggested_fix = "Catch a narrower exception or add context before re-raising."
            elif "print(" in changed_lines and profile.get("style"):
                severity = "nitpick"
                category = "convention"
                comment = "The repository style profile suggests a preference for consistent formatting; this hunk may need a quick cleanup."
                suggested_fix = "Format the change to match the repository's existing style."

            llm_override = self._llm_review_override(llm_client, hunk, profile, changed_lines)
            if llm_override is not None:
                severity = llm_override.get("severity", severity)
                category = llm_override.get("category", category)
                comment = llm_override.get("comment", comment)
                suggested_fix = llm_override.get("suggested_fix", suggested_fix)

            comments.append(
                {
                    "file": hunk.get("file", ""),
                    "line": line,
                    "severity": severity,
                    "category": category,
                    "comment": comment,
                    "suggested_fix": suggested_fix,
                    "profile": profile,
                }
            )

        return comments

    def render_markdown(self, comments: List[Dict[str, Any]]) -> str:
        if not comments:
            return "No review comments generated."

        sections: List[str] = []
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for comment in comments:
            grouped.setdefault(comment["file"], []).append(comment)

        for file_name, file_comments in sorted(grouped.items()):
            sections.append(f"### {file_name}")
            for comment in file_comments:
                sections.append(
                    f"- **{comment['severity'].upper()}** [{comment['category']}] line {comment['line']}: {comment['comment']}"
                )
                sections.append(f"  - Suggested fix: {comment['suggested_fix']}")
            sections.append("")

        return "\n".join(sections).strip()
