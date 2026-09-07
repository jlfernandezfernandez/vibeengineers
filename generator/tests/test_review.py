"""Tests for the pipeline review loop."""
from unittest.mock import MagicMock, patch

from article_generator.pipeline import run


def env(max_rounds="2"):
    return {
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_TOKEN": "tok",
        "OLLAMA_BASE_URL": "https://ollama.com/v1",
        "OLLAMA_API_KEY": "k",
        "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY": "k",
        "AGENT_WRITER_PROVIDER": "ollama",
        "AGENT_WRITER_MODEL": "writer-m",
        "AGENT_REVIEWER_PROVIDER": "openrouter",
        "AGENT_REVIEWER_MODEL": "reviewer-m",
        "MAX_REVIEW_ROUNDS": max_rounds,
        "PR_NUMBER": "9",
        "SITE_URL": "https://owner.github.io/repo",
    }


FRONTMATTER = '---\ntitle: "Vistas"\ntags: ["snowflake"]\nwriter: "writer-m"\n---\n\n'
ARTICLE_BODY = "## Contexto\n\nContenido técnico."
PATH = "frontend/src/content/blog/vistas.md"
APPROVED = {"issues": []}


def issue(detail="falta import de Flux", blocking=True):
    return {"category": "codigo", "blocking": blocking, "detail": detail}


def setup_github(github_cls):
    github = github_cls.return_value
    github.get_pr.return_value = {
        "body": "Closes #5",
        "head": {"ref": "article/issue-5"},
        "title": "article: Vistas",
    }
    github.get_article_path.return_value = PATH
    github.read_file.return_value = FRONTMATTER + ARTICLE_BODY
    return github


def setup_llms(llm_cls, reports, fixes=()):
    reviewer, writer_chat = MagicMock(), MagicMock()
    reviewer.generate_structured.side_effect = reports
    writer_chat.generate.side_effect = fixes
    llm_cls.side_effect = lambda base_url, api_key, model: (
        reviewer if model == "reviewer-m" else writer_chat
    )
    return reviewer, writer_chat


@patch("article_generator.pipeline._body_defects", return_value=[])
@patch("article_generator.pipeline.LLMClient")
@patch("article_generator.pipeline.GitHubClient")
def test_approved_first_round_merges_and_closes_issue(github_cls, llm_cls, _body_defects):
    github = setup_github(github_cls)
    setup_llms(llm_cls, [APPROVED])

    assert run(env()) == 0

    github.merge_pr.assert_called_once_with(9, branch="article/issue-5")
    assert '\nreviewer: "reviewer-m"\n' in github.update_file.call_args.args[2]
    assert github.close_with_comment.call_args.args[1].endswith("/blog/vistas/")


@patch("article_generator.pipeline._body_defects", return_value=[])
@patch("article_generator.pipeline.LLMClient")
@patch("article_generator.pipeline.GitHubClient")
def test_blocking_defect_writer_fixes_then_merges(github_cls, llm_cls, _body_defects):
    github = setup_github(github_cls)
    _, writer_chat = setup_llms(
        llm_cls, [{"issues": [issue()]}, APPROVED], fixes=[ARTICLE_BODY]
    )

    assert run(env()) == 0

    assert "falta import de Flux" in writer_chat.generate.call_args.args[1]
    github.merge_pr.assert_called_once()


@patch("article_generator.pipeline._body_defects", return_value=[])
@patch("article_generator.pipeline.LLMClient")
@patch("article_generator.pipeline.GitHubClient")
def test_round_budget_exhausted_leaves_pr_open(github_cls, llm_cls, _body_defects):
    github = setup_github(github_cls)
    rejected = {"issues": [issue("API inexistente")]}
    setup_llms(llm_cls, [rejected, rejected], fixes=[ARTICLE_BODY])

    assert run(env(max_rounds="1")) == 0

    github.merge_pr.assert_not_called()
    assert "API inexistente" in github.comment.call_args.args[1]


@patch("article_generator.pipeline._body_defects", return_value=[])
@patch("article_generator.pipeline.LLMClient")
@patch("article_generator.pipeline.GitHubClient")
def test_suggestions_do_not_block_publication(github_cls, llm_cls, _body_defects):
    github = setup_github(github_cls)
    setup_llms(llm_cls, [{"issues": [issue("mejor ejemplo", blocking=False)]}])

    assert run(env()) == 0

    assert "sugerencias" in github.comment.call_args.args[1]
    github.merge_pr.assert_called_once()


@patch("article_generator.pipeline.LLMClient")
@patch("article_generator.pipeline.GitHubClient")
def test_oversize_article_blocks_without_calling_reviewer(github_cls, llm_cls):
    github = setup_github(github_cls)
    long_body = "## Sección\n\n" + ("palabra " * 1400)
    github.read_file.return_value = FRONTMATTER + long_body
    setup_llms(llm_cls, [APPROVED])

    assert run(env(max_rounds="0")) == 0

    github.merge_pr.assert_not_called()
    comment = github.comment.call_args.args[1]
    assert "[legibilidad]" in comment
    assert "1401" in comment and "1300" in comment


@patch("article_generator.pipeline._body_defects", return_value=[])
@patch("article_generator.pipeline.LLMClient")
@patch("article_generator.pipeline.GitHubClient")
def test_broken_reviewer_escalates_without_writer_fix(github_cls, llm_cls, _body_defects):
    from article_generator.llm import LLMError

    github = setup_github(github_cls)
    setup_llms(llm_cls, [LLMError("down"), LLMError("down")])

    assert run(env()) == 0

    github.merge_pr.assert_not_called()
    assert "no devolvió un informe válido" in github.comment.call_args.args[1]
