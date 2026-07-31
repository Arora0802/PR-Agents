from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pr_review_agent.review_agent import PRReviewAgent


def main() -> int:
    parser = argparse.ArgumentParser(description="Review a PR diff against a repository convention profile")
    parser.add_argument("repo_path", nargs="?", default=".", help="Path to the repository to review")
    parser.add_argument("diff_path", nargs="?", help="Optional path to a diff file; otherwise reads stdin")
    args = parser.parse_args()

    repo_path = Path(args.repo_path).resolve()
    agent = PRReviewAgent(repo_path)

    if args.diff_path:
        diff_text = Path(args.diff_path).read_text(encoding="utf-8")
    else:
        diff_text = sys.stdin.read()

    comments = agent.review_diff(diff_text)
    print(agent.render_markdown(comments))
    return 0


if __name__ == "__main__":
    main()
