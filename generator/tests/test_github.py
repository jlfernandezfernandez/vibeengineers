"""Tests for the GitHub client: topic queue and article PRs."""
import base64
from unittest.mock import MagicMock, patch

import pytest

from article_generator.github import GitHubClient, GitHubError


def issue(number, created, labels, title="t", votes=0):
    return {
        "number": number,
        "title": title,
        "body": "notes",
        "created_at": created,
        "labels": [{"name": name} for name in labels],
        "reactions": {"+1": votes},
    }


def make_client(get_payload=None, get_status=200):
    c = GitHubClient(repo="owner/repo", token="tok")
    c.session = MagicMock()
    resp = MagicMock(status_code=get_status, text="err")
    resp.json.return_value = get_payload if get_payload is not None else []
    c.session.get.return_value = resp
    c.session.post.return_value = MagicMock(status_code=201)
    c.session.put.return_value = MagicMock(status_code=200)
    c.session.patch.return_value = MagicMock(status_code=200)
    return c


# --- Topic queue ---


def test_next_topic_returns_oldest():
    c = make_client([
        issue(2, "2026-06-02T00:00:00Z", ["topic"]),
        issue(1, "2026-06-01T00:00:00Z", ["topic"]),
    ])
    assert c.next_topic()["number"] == 1


def test_next_topic_priority_jumps_queue():
    c = make_client([
        issue(1, "2026-06-01T00:00:00Z", ["topic"]),
        issue(2, "2026-06-02T00:00:00Z", ["topic", "priority"]),
    ])
    assert c.next_topic()["number"] == 2


def test_next_topic_most_voted_wins():
    c = make_client([
        issue(1, "2026-06-01T00:00:00Z", ["topic"], votes=1),
        issue(2, "2026-06-02T00:00:00Z", ["topic"], votes=5),
    ])
    assert c.next_topic()["number"] == 2


def test_next_topic_votes_tie_falls_back_to_oldest():
    c = make_client([
        issue(2, "2026-06-02T00:00:00Z", ["topic"], votes=3),
        issue(1, "2026-06-01T00:00:00Z", ["topic"], votes=3),
    ])
    assert c.next_topic()["number"] == 1


def test_next_topic_priority_beats_votes():
    c = make_client([
        issue(1, "2026-06-01T00:00:00Z", ["topic"], votes=99),
        issue(2, "2026-06-02T00:00:00Z", ["topic", "priority"], votes=0),
    ])
    assert c.next_topic()["number"] == 2


def test_next_topic_none_when_empty():
    assert make_client([]).next_topic() is None


def test_next_topic_skips_pull_requests():
    pr = issue(3, "2026-05-01T00:00:00Z", ["topic"])
    pr["pull_request"] = {"url": "x"}
    c = make_client([pr, issue(1, "2026-06-01T00:00:00Z", ["topic"])])
    assert c.next_topic()["number"] == 1


def test_next_topic_skips_issues_with_open_article_pr():
    c = make_client([
        issue(1, "2026-06-01T00:00:00Z", ["topic"]),
        issue(2, "2026-06-02T00:00:00Z", ["topic"]),
    ])
    assert c.next_topic(skip={1})["number"] == 2


def test_next_topic_none_when_all_have_open_article_pr():
    c = make_client([issue(1, "2026-06-01T00:00:00Z", ["topic"])])
    assert c.next_topic(skip={1}) is None


def test_next_topic_raises_on_api_error():
    with pytest.raises(GitHubError):
        make_client([], get_status=500).next_topic()


def test_get_issue_returns_issue():
    payload = issue(7, "2026-06-11T08:00:00Z", ["triage"])
    c = make_client(payload)
    assert c.get_issue(7) == payload


def test_update_issue_patches_title_and_body():
    c = make_client()
    c.update_issue(7, title="Pydantic AI", body="Mejor descripción")
    c.session.patch.assert_called_once_with(
        "https://api.github.com/repos/owner/repo/issues/7",
        json={"title": "Pydantic AI", "body": "Mejor descripción"},
    )


def test_set_labels_replaces_all_issue_labels():
    c = make_client()
    c.set_labels(7, ["topic"])
    c.session.put.assert_called_once_with(
        "https://api.github.com/repos/owner/repo/issues/7/labels",
        json={"labels": ["topic"]},
    )


def test_comment_posts_to_issue():
    c = make_client()
    c.comment(9, "review comment")
    c.session.post.assert_called_once_with(
        "https://api.github.com/repos/owner/repo/issues/9/comments",
        json={"body": "review comment"},
    )


def test_close_with_comment_comments_labels_then_closes():
    c = make_client()
    c.close_with_comment(7, "done")
    posts = c.session.post.call_args_list
    assert posts[0].kwargs == {"json": {"body": "done"}}
    assert posts[1].kwargs == {"json": {"labels": ["published"]}}
    c.session.patch.assert_called_once_with(
        "https://api.github.com/repos/owner/repo/issues/7",
        json={"state": "closed"},
    )


def test_close_with_comment_tolerates_label_failure():
    c = make_client()
    ok = MagicMock(status_code=201)
    label_fail = MagicMock(status_code=404)
    c.session.post.side_effect = [ok, label_fail]
    c.close_with_comment(7, "done")  # must not raise
    c.session.patch.assert_called_once()


# --- Article pull requests ---


def pr_client():
    c = make_client({"object": {"sha": "abc123"}})  # ref lookup
    branch = MagicMock(status_code=201)
    pr = MagicMock(status_code=201)
    pr.json.return_value = {"html_url": "https://github.com/owner/repo/pull/9", "number": 9}
    c.session.post.side_effect = [branch, pr]
    c.session.put.return_value = MagicMock(status_code=201)
    return c


def test_open_pr_creates_branch_file_and_pr():
    c = pr_client()
    url, number = c.open_pr(
        branch="article/issue-5",
        path="frontend/src/content/blog/2026-06-11-tema.md",
        content="---\ntitle: x\n---\n\ncuerpo\n",
        title="article: tema",
        body="Closes #5",
    )
    assert url == "https://github.com/owner/repo/pull/9"
    assert number == 9

    sent = c.session.put.call_args.kwargs["json"]
    assert sent["branch"] == "article/issue-5"
    assert base64.b64decode(sent["content"]).decode() == "---\ntitle: x\n---\n\ncuerpo\n"

    pr_call = c.session.post.call_args_list[1]
    assert pr_call.kwargs["json"] == {
        "title": "article: tema",
        "head": "article/issue-5",
        "base": "main",
        "body": "Closes #5",
    }


def test_open_pr_returns_existing_when_branch_exists():
    c = pr_client()
    c.session.post.side_effect = [MagicMock(status_code=422, text="Reference already exists")]
    existing_pr = MagicMock(status_code=200)
    existing_pr.json.return_value = [{"html_url": "https://github.com/owner/repo/pull/9", "number": 9}]
    c.session.get.side_effect = [
        MagicMock(status_code=200, json=lambda: {"object": {"sha": "abc"}}),  # ref lookup
        existing_pr,  # find existing PR
    ]
    url, number = c.open_pr(
        branch="article/issue-5", path="p.md", content="x", title="t", body="b"
    )
    assert url == "https://github.com/owner/repo/pull/9"
    assert number == 9


def test_open_pr_raises_when_branch_exists_but_no_pr_found():
    c = pr_client()
    c.session.post.side_effect = [MagicMock(status_code=422, text="Reference already exists")]
    no_pr = MagicMock(status_code=200)
    no_pr.json.return_value = []
    c.session.get.side_effect = [
        MagicMock(status_code=200, json=lambda: {"object": {"sha": "abc"}}),
        no_pr,
    ]
    with pytest.raises(GitHubError, match="no open PR found"):
        c.open_pr(branch="article/issue-5", path="p.md", content="x", title="t", body="b")


def test_open_pr_raises_on_ref_lookup_error():
    c = pr_client()
    c.session.get.return_value = MagicMock(status_code=404, text="nope")
    with pytest.raises(GitHubError):
        c.open_pr(branch="b", path="p.md", content="x", title="t", body="b")


def test_open_article_issue_numbers_parses_article_branches():
    c = make_client([
        {"head": {"ref": "article/issue-5"}},
        {"head": {"ref": "article/issue-12"}},
        {"head": {"ref": "fix/typo"}},
    ])
    assert c.open_article_issue_numbers() == {5, 12}


def test_open_article_issue_numbers_empty_when_no_prs():
    assert make_client([]).open_article_issue_numbers() == set()


def test_get_article_path_finds_md():
    c = make_client([
        {"filename": "frontend/src/content/blog/2026-06-11-tema.md", "status": "added"},
        {"filename": "README.md", "status": "modified"},
    ])
    assert c.get_article_path(9) == "frontend/src/content/blog/2026-06-11-tema.md"


def test_get_article_path_raises_when_no_md():
    c = make_client([{"filename": "README.md", "status": "modified"}])
    with pytest.raises(GitHubError, match="No article"):
        c.get_article_path(9)


def test_read_file_decodes_content():
    content = base64.b64encode(b"hello world").decode()
    c = make_client({"content": content})
    assert c.read_file("main", "frontend/src/content/blog/test.md") == "hello world"


def test_merge_pr_squashes():
    c = make_client()
    c.merge_pr(9)
    c.session.put.assert_called_once_with(
        "https://api.github.com/repos/owner/repo/pulls/9/merge",
        json={"merge_method": "squash"},
    )
    c.session.delete.assert_not_called()


def test_merge_pr_updates_branch_on_conflict():
    c = make_client()
    conflict = MagicMock(status_code=409)
    ok = MagicMock(status_code=200)
    update_ok = MagicMock(status_code=202)
    c.session.put.side_effect = [conflict, update_ok, ok]
    c.merge_pr(9)
    assert c.session.put.call_count == 3
    c.session.put.assert_any_call(
        "https://api.github.com/repos/owner/repo/pulls/9/update-branch",
        json={},
    )


def test_merge_pr_deletes_branch_after_merge():
    c = make_client()
    c.merge_pr(9, branch="article/issue-5")
    c.session.delete.assert_called_once_with(
        "https://api.github.com/repos/owner/repo/git/refs/heads/article/issue-5"
    )


def test_merge_pr_failure_does_not_delete_branch():
    c = make_client()
    c.session.put.return_value = MagicMock(status_code=404, text="err")
    with patch("article_generator.github.time.sleep"):
        with pytest.raises(GitHubError):
            c.merge_pr(9)
    c.session.delete.assert_not_called()


def test_update_file_reuses_existing_sha():
    c = make_client({"sha": "oldsha"})
    c.update_file("article/issue-5", "frontend/src/content/blog/file.md", "new content", "fix: review")
    sent = c.session.put.call_args.kwargs["json"]
    assert sent["sha"] == "oldsha"
    assert base64.b64decode(sent["content"]).decode() == "new content"
    assert sent["branch"] == "article/issue-5"
