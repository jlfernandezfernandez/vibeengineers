"""GitHub API client: the topic queue (issues) and article pull requests.

An open issue labeled `topic` is a pending topic. `priority` jumps the
queue. Closing the issue with a link marks it published.

Article PRs use the contents API instead of local git: the run must not
leave uncommitted files behind for the publish step, and the PR is the
approval mechanism (merge = publish, close = discard). A PR left open
means the reviewer could not approve it and a human has to decide.
"""
import base64
import re
import time

import requests

TOPIC_LABEL = "topic"
TRIAGE_LABEL = "triage"
PRIORITY_LABEL = "priority"
PUBLISHED_LABEL = "published"
REJECTED_LABEL = "rejected"

BRANCH_PATTERN = re.compile(r"^article/issue-(\d+)$")


class GitHubError(Exception):
    pass


class GitHubClient:
    def __init__(self, repo: str, token: str):
        self.repo = repo
        self.base = f"https://api.github.com/repos/{repo}"
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    def _require(self, response, expected: tuple[int, ...], action: str) -> None:
        if response.status_code not in expected:
            raise GitHubError(
                f"Failed to {action}: GitHub API error {response.status_code}: "
                f"{response.text[:500]}"
            )

    def article_exists_for_date(self, date_prefix: str) -> bool:
        """Return True if an article file for the given date already exists on main."""
        resp = self.session.get(
            f"{self.base}/contents/frontend/src/content/blog",
            params={"ref": "main", "per_page": 100},
        )
        if resp.status_code == 404:
            return False
        self._require(resp, (200,), "list blog directory")
        for entry in resp.json():
            if entry.get("type") == "file" and entry["name"].startswith(date_prefix) and entry["name"].endswith(".md"):
                return True
        return False

    # --- Topic queue (issues) ---

    def next_topic(self, skip: set[int] = frozenset()) -> dict | None:
        """Next topic to publish; `skip` holds issues whose article PR is already open."""
        resp = self.session.get(
            f"{self.base}/issues",
            params={"state": "open", "labels": TOPIC_LABEL, "per_page": 100},
        )
        self._require(resp, (200,), "list open topics")
        issues = [
            i
            for i in resp.json()
            if "pull_request" not in i and i["number"] not in skip
        ]
        if not issues:
            return None
        priority = [
            i for i in issues
            if any(label["name"] == PRIORITY_LABEL for label in i["labels"])
        ]
        pool = priority or issues
        # Most 👍 reactions wins; ties go to the oldest issue.
        return min(pool, key=lambda i: (-i.get("reactions", {}).get("+1", 0), i["created_at"]))

    def get_issue(self, number: int) -> dict:
        resp = self.session.get(f"{self.base}/issues/{number}")
        self._require(resp, (200,), f"get issue #{number}")
        return resp.json()

    def update_issue(self, number: int, *, title: str, body: str) -> None:
        resp = self.session.patch(
            f"{self.base}/issues/{number}", json={"title": title, "body": body}
        )
        self._require(resp, (200,), f"update issue #{number}")

    def set_labels(self, number: int, labels: list[str]) -> None:
        resp = self.session.put(
            f"{self.base}/issues/{number}/labels",
            json={"labels": labels},
        )
        self._require(resp, (200,), f"set labels on issue #{number}")

    def comment(self, number: int, body: str) -> None:
        """Comment on an issue or a PR (PRs are issues in the GitHub API)."""
        resp = self.session.post(
            f"{self.base}/issues/{number}/comments",
            json={"body": body},
        )
        self._require(resp, (200, 201), f"comment on #{number}")

    def close(self, number: int) -> None:
        resp = self.session.patch(
            f"{self.base}/issues/{number}",
            json={"state": "closed"},
        )
        self._require(resp, (200,), f"close issue #{number}")

    def close_with_comment(self, number: int, comment: str) -> None:
        self.comment(number, comment)
        # Best effort: a missing label must not block publishing.
        resp = self.session.post(
            f"{self.base}/issues/{number}/labels", json={"labels": [PUBLISHED_LABEL]}
        )
        if resp.status_code not in (200, 201):
            print(f"Could not label issue #{number} as {PUBLISHED_LABEL}: {resp.status_code}")
        self.close(number)

    # --- Article pull requests ---

    def open_pr(
        self, branch: str, path: str, content: str, title: str, body: str, base: str = "main"
    ) -> tuple[str, int]:
        ref = self.session.get(f"{self.base}/git/ref/heads/{base}")
        self._require(ref, (200,), f"resolve {base} ref")
        sha = ref.json()["object"]["sha"]

        created = self.session.post(
            f"{self.base}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": sha},
        )
        if created.status_code == 422:
            # Branch already exists (likely a re-run). Return the existing PR.
            existing = self.session.get(
                f"{self.base}/pulls",
                params={"state": "open", "head": f"{self.repo}:{branch}"},
            )
            self._require(existing, (200,), f"find existing PR for branch {branch}")
            prs_list = existing.json()
            if prs_list:
                pr = prs_list[0]
                return pr["html_url"], pr["number"]
            raise GitHubError(
                f"Branch {branch} exists but no open PR found; close or delete it first"
            )
        self._require(created, (201,), f"create branch {branch}")

        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        put = self.session.put(
            f"{self.base}/contents/{path}",
            json={"message": f"article: {title}", "content": encoded, "branch": branch},
        )
        self._require(put, (201,), f"create {path} on {branch}")

        pr = self.session.post(
            f"{self.base}/pulls",
            json={"title": title, "head": branch, "base": base, "body": body},
        )
        self._require(pr, (201,), "open pull request")
        return pr.json()["html_url"], pr.json()["number"]

    def open_article_issue_numbers(self) -> set[int]:
        """Issue numbers that already have an open article PR (pending review)."""
        resp = self.session.get(
            f"{self.base}/pulls", params={"state": "open", "per_page": 100}
        )
        self._require(resp, (200,), "list open pull requests")
        numbers = set()
        for pr in resp.json():
            match = BRANCH_PATTERN.match(pr["head"]["ref"])
            if match:
                numbers.add(int(match.group(1)))
        return numbers

    def get_pr(self, number: int) -> dict:
        resp = self.session.get(f"{self.base}/pulls/{number}")
        self._require(resp, (200,), f"get PR #{number}")
        return resp.json()

    def get_article_path(self, pr_number: int) -> str:
        """Find the .md file added by this PR under frontend/src/content/blog/."""
        resp = self.session.get(f"{self.base}/pulls/{pr_number}/files")
        self._require(resp, (200,), f"list files in PR #{pr_number}")
        for f in resp.json():
            if f["filename"].startswith("frontend/src/content/blog/") and f["filename"].endswith(".md"):
                return f["filename"]
        raise GitHubError(f"No article .md found in PR #{pr_number}")

    def read_file(self, ref: str, path: str) -> str:
        """Read file content from a branch via the contents API."""
        resp = self.session.get(
            f"{self.base}/contents/{path}",
            params={"ref": ref},
        )
        self._require(resp, (200,), f"read {path} at {ref}")
        return base64.b64decode(resp.json()["content"]).decode("utf-8")

    def merge_pr(self, number: int, branch: str = "") -> None:
        resp = self.session.put(f"{self.base}/pulls/{number}/merge", json={"merge_method": "squash"})
        if resp.status_code == 409:
            self.session.put(
                f"{self.base}/pulls/{number}/update-branch",
                json={},
            )
            resp = self.session.put(f"{self.base}/pulls/{number}/merge", json={"merge_method": "squash"})
        # 405: not mergeable (CI pending, etc). Retry a few times.
        for _ in range(5):
            if resp.status_code == 405:
                time.sleep(15)
                resp = self.session.put(
                    f"{self.base}/pulls/{number}/merge", json={"merge_method": "squash"}
                )
            else:
                break
        self._require(resp, (200,), f"merge PR #{number}")
        if branch:
            # Best effort: a leftover branch only blocks a future rerun of the
            # same issue, so don't fail the publish over it.
            self.session.delete(f"{self.base}/git/refs/heads/{branch}")

    def update_file(self, branch: str, path: str, content: str, message: str) -> None:
        resp = self.session.get(
            f"{self.base}/contents/{path}",
            params={"ref": branch},
        )
        self._require(resp, (200,), f"get SHA for {path}")
        sha = resp.json()["sha"]
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        put = self.session.put(
            f"{self.base}/contents/{path}",
            json={"message": message, "content": encoded, "branch": branch, "sha": sha},
        )
        self._require(put, (200,), f"update {path} on {branch}")
