"""Tests for the end-to-end editorial pipeline."""
from unittest.mock import MagicMock, patch

from article_generator.pipeline import run


def env(pr_number=""):
    return {
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_TOKEN": "tok",
        "OLLAMA_BASE_URL": "https://ollama.com/v1",
        "OLLAMA_API_KEY": "k",
        "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY": "k",
        "AGENT_WRITER_PROVIDER": "ollama",
        "AGENT_WRITER_MODEL": "writer-m",
        "AGENT_WRITER_JSON_PROVIDER": "openrouter",
        "AGENT_WRITER_JSON_MODEL": "writer-json-m",
        "AGENT_REVIEWER_PROVIDER": "openrouter",
        "AGENT_REVIEWER_MODEL": "reviewer-m",
        "MAX_REVIEW_ROUNDS": "2",
        "PR_NUMBER": pr_number,
        "SITE_URL": "https://owner.github.io/repo",
    }


def topic_issue():
    return {
        "number": 5,
        "title": "Project Reactor",
        "body": "Explica backpressure y trade-offs.",
        "user": {"login": "jordi"},
    }


def setup_llms(llm_cls):
    writer_chat = MagicMock()
    writer_chat.generate.side_effect = [
        "## Contexto\n\nArtículo.",
        "## Contexto\n\nCorregido.",
    ]
    writer_json = MagicMock()
    writer_json.generate_structured.return_value = {
        "title": "Project Reactor y backpressure",
        "summary": "Cómo funciona el control de demanda.",
        "tags": ["auth"],
        "quiz": [
            {
                "question": f"Pregunta {i}?",
                "options": ["A", "B", "C", "D"],
                "correct": 1,
                "explanation": "Porque B.",
            }
            for i in range(3)
        ],
    }
    reviewer = MagicMock()
    reviewer.generate_structured.return_value = {"issues": []}

    def make_client(base_url, api_key, model):
        if model == "reviewer-m":
            return reviewer
        if model == "writer-json-m":
            return writer_json
        return writer_chat

    llm_cls.side_effect = make_client
    return writer_chat, writer_json, reviewer


@patch("article_generator.pipeline._body_defects", return_value=[])
@patch("article_generator.pipeline._canonical_tags", return_value=["reactive"])
@patch("article_generator.pipeline.LLMClient")
@patch("article_generator.pipeline.GitHubClient")
def test_pipeline_opens_draft_pr_before_review_and_merges(
    github_cls, llm_cls, _canonical_tags, _body_defects
):
    github = github_cls.return_value
    github.article_exists_for_date.return_value = False
    github.next_topic.return_value = topic_issue()
    github.open_article_issue_numbers.return_value = set()
    github.open_pr.return_value = ("https://github.com/owner/repo/pull/9", 9)
    github.get_pr.return_value = {
        "body": "Closes #5",
        "head": {"ref": "article/issue-5"},
        "title": "article: Project Reactor y backpressure",
    }
    github.get_article_path.return_value = "frontend/src/content/blog/reactor.md"
    github.read_file.return_value = (
        '---\ntitle: "Project Reactor y backpressure"\ntags: ["auth"]\nwriter: "writer-m"\n---\n\n'
        "## Contexto\n\nArtículo."
    )
    setup_llms(llm_cls)

    assert run(env()) == 0

    github.open_pr.assert_called_once()
    assert github.open_pr.call_args.kwargs["path"].startswith("frontend/src/content/blog/")
    created = github.open_pr.call_args.kwargs["content"]
    assert 'tags: ["auth"]' in created
    taxonomy_update = next(
        call for call in github.update_file.call_args_list if call.args[1] == "frontend/src/data/tags.json"
    )
    assert taxonomy_update.args[2] == '[\n  "auth",\n  "reactive"\n]\n'
    calls = [method[0] for method in github.method_calls]
    assert calls.index("open_pr") < calls.index("get_pr")
    github.merge_pr.assert_called_once_with(9, branch="article/issue-5")


@patch("article_generator.pipeline._body_defects", return_value=[])
@patch("article_generator.pipeline._canonical_tags", return_value=["reactive"])
@patch("article_generator.pipeline.LLMClient")
@patch("article_generator.pipeline.GitHubClient")
def test_pipeline_does_not_update_taxonomy_when_writer_reuses_tag(
    github_cls, llm_cls, _canonical_tags, _body_defects
):
    github = github_cls.return_value
    github.article_exists_for_date.return_value = False
    github.next_topic.return_value = topic_issue()
    github.open_article_issue_numbers.return_value = set()
    github.open_pr.return_value = ("https://github.com/owner/repo/pull/9", 9)
    github.get_pr.return_value = {
        "body": "Closes #5",
        "head": {"ref": "article/issue-5"},
        "title": "article: Project Reactor",
    }
    github.get_article_path.return_value = "frontend/src/content/blog/reactor.md"
    github.read_file.return_value = (
        '---\ntitle: "Project Reactor"\ntags: ["reactive"]\nwriter: "writer-m"\n---\n\n'
        "## Contexto\n\nArtículo."
    )
    writer_chat, writer_json, _ = setup_llms(llm_cls)
    writer_json.generate_structured.return_value["tags"] = ["reactive"]

    assert run(env()) == 0

    taxonomy_updates = [
        call for call in github.update_file.call_args_list if call.args[1] == "frontend/src/data/tags.json"
    ]
    assert taxonomy_updates == []


@patch("article_generator.pipeline.LLMClient")
@patch("article_generator.pipeline.GitHubClient")
def test_pipeline_skips_when_queue_is_empty(github_cls, llm_cls):
    github_cls.return_value.article_exists_for_date.return_value = False
    github_cls.return_value.next_topic.return_value = None

    assert run(env()) == 0

    llm_cls.assert_not_called()


@patch("article_generator.pipeline.LLMClient")
@patch("article_generator.pipeline.GitHubClient")
def test_pipeline_skips_when_article_already_published_today(github_cls, llm_cls):
    github_cls.return_value.article_exists_for_date.return_value = True

    assert run(env()) == 0

    github_cls.return_value.next_topic.assert_not_called()
    llm_cls.assert_not_called()


@patch("article_generator.pipeline._review_draft", return_value=0)
@patch("article_generator.pipeline.GitHubClient")
def test_manual_run_reviews_existing_pr(github_cls, review):
    assert run(env("17")) == 0
    review.assert_called_once_with(env("17"), github_cls.return_value, 17)
