"""Editorial pipeline: coordinates agents, validation and GitHub side effects."""
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

from .agents.reviewer import review_article, split_issues
from .agents.triage import Classification, TriageError, classify
from .agents.writer import revise_article, write_article
from .article import (
    MAX_WORDS_PER_ARTICLE,
    ValidationError,
    parse_title_and_tags,
    render_article,
    sign_reviewer,
    slugify,
    split_frontmatter,
    validate_body,
    word_count,
)
from .config import AppConfig, load_config
from .github import (
    PRIORITY_LABEL,
    REJECTED_LABEL,
    TOPIC_LABEL,
    TRIAGE_LABEL,
    GitHubClient,
)
from .llm import LLMClient, LLMError


TAGS_FILE = Path("frontend/src/data/tags.json")


def _client(config: AppConfig, name: str) -> LLMClient:
    agent = config.agents[name]
    provider = config.providers[agent.provider]
    return LLMClient(base_url=provider.base_url, api_key=provider.api_key, model=agent.model)


def _canonical_tags() -> list[str]:
    try:
        tags = json.loads(TAGS_FILE.read_text(encoding="utf-8"))
        return [tag for tag in tags if isinstance(tag, str)]
    except (OSError, ValueError):
        return []


def _body_defects(body: str) -> list[str]:
    defects = []
    try:
        validate_body(body)
    except ValidationError as exc:
        defects.append(f"[validacion] {exc}")
    words = word_count(body)
    if words > MAX_WORDS_PER_ARTICLE:
        defects.append(
            f"[legibilidad] El artículo tiene {words} palabras; el techo es "
            f"{MAX_WORDS_PER_ARTICLE} (~5 min). Recorta secciones secundarias."
        )
    return defects


def _issue_number(pr_body: str) -> int | None:
    match = re.search(r"Closes\s+#(\d+)", pr_body)
    return int(match.group(1)) if match else None


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _publish_url(site_url: str, path: str) -> str:
    slug = path.removeprefix("frontend/src/content/blog/").removesuffix(".md")
    return f"{site_url}/blog/{slug}/" if site_url else slug


def _open_draft(env: dict, github: GitHubClient) -> int | None:
    today = date.today().isoformat()
    if github.article_exists_for_date(today):
        print(f"Article for {today} already exists; skipping.")
        return None
    issue = github.next_topic(skip=github.open_article_issue_numbers())
    if issue is None:
        print("No pending topics; nothing to publish.")
        return None

    config = load_config(env, "WRITER", "WRITER_JSON")
    writer_chat = _client(config, "WRITER")
    writer_json = _client(config, "WRITER_JSON")
    canonical_tags = _canonical_tags()
    draft = write_article(
        writer_chat, writer_json, issue["title"], issue.get("body") or "", canonical_tags
    )
    initial_defects = _body_defects(draft.body)
    if initial_defects:
        print("Generated article failed validation:")
        for defect in initial_defects:
            print(f"  - {defect}")
        return None
    content = render_article(
        pub_date=date.today(),
        title=draft.title,
        description=draft.summary,
        tags=draft.tags,
        body=draft.body,
        quiz=draft.quiz,
        issue_number=issue["number"],
        requested_by=(issue.get("user") or {}).get("login", ""),
        writer=config.agents["WRITER"].model,
    )
    url, pr_number = github.open_pr(
        branch=f"article/issue-{issue['number']}",
        path=f"frontend/src/content/blog/{date.today().isoformat()}-{slugify(draft.title)}.md",
        content=content,
        title=f"article: {draft.title}",
        body=f"Closes #{issue['number']}",
    )
    taxonomy = sorted(set(canonical_tags) | set(draft.tags))
    if taxonomy != sorted(canonical_tags):
        github.update_file(
            f"article/issue-{issue['number']}",
            str(TAGS_FILE),
            json.dumps(taxonomy, indent=2, ensure_ascii=False) + "\n",
            "chore: add article tags",
        )
    print(f"Draft PR #{pr_number} opened before review: {url}")
    return pr_number


def _review_draft(env: dict, github: GitHubClient, pr_number: int) -> int:
    pr = github.get_pr(pr_number)
    branch = pr["head"]["ref"]
    issue_number = _issue_number(pr.get("body") or "")
    path = github.get_article_path(pr_number)
    content = github.read_file(branch, path)
    frontmatter, body = split_frontmatter(content)
    title, _ = parse_title_and_tags(content)
    topic = title or pr["title"].removeprefix("article: ").strip()

    config = load_config(env, "REVIEWER", "WRITER")
    reviewer = _client(config, "REVIEWER")
    writer_chat = _client(config, "WRITER")
    max_rounds = int(env["MAX_REVIEW_ROUNDS"])

    def escalate(reason: str, pending: list[str]) -> int:
        details = f"\n\nDefectos pendientes:\n\n{_bullets(pending)}" if pending else ""
        github.comment(
            pr_number,
            f"{reason}{details}\n\nMergear publica el artículo; cerrar la PR lo descarta.",
        )
        print("Could not approve. PR left open for a human.")
        return 0

    for fixes_done in range(max_rounds + 1):
        blocking = _body_defects(body)
        suggestions: list[str] = []
        if not blocking:
            report = review_article(reviewer, topic, f"# {topic}\n\n{body}")
            if report is None:
                return escalate("El reviewer no devolvió un informe válido.", [])
            blocking, suggestions = split_issues(report)

        if not blocking:
            if suggestions:
                github.comment(
                    pr_number, f"Aprobado con sugerencias no bloqueantes:\n\n{_bullets(suggestions)}"
                )
            signed = sign_reviewer(frontmatter, config.agents["REVIEWER"].model)
            if signed != frontmatter:
                github.update_file(branch, path, signed + body, "chore: reviewer sign-off")
            github.merge_pr(pr_number, branch=branch)
            if issue_number:
                note = " (con correcciones)" if fixes_done else ""
                github.close_with_comment(
                    issue_number,
                    f"Publicado{note}: {_publish_url(env.get('SITE_URL', '').rstrip('/'), path)}",
                )
            print(f"Approved after {fixes_done} fix(es). Merged.")
            return 0

        if fixes_done == max_rounds:
            return escalate(
                f"El reviewer sigue sin aprobar tras {max_rounds} correcciones.", blocking
            )

        round_number = fixes_done + 1
        github.comment(
            pr_number, f"Cambios solicitados (ronda {round_number}):\n\n{_bullets(blocking)}"
        )
        fixed = revise_article(writer_chat, topic, body, blocking)
        if _body_defects(fixed):
            return escalate("La corrección del writer produjo Markdown inválido.", blocking)

        github.update_file(branch, path, frontmatter + fixed, f"fix: review feedback (round {round_number})")
        body = fixed
    return 0


def run(env: dict) -> int:
    github = GitHubClient(repo=env["GITHUB_REPOSITORY"], token=env["GITHUB_TOKEN"])
    pr_number = int(env["PR_NUMBER"]) if env.get("PR_NUMBER") else _open_draft(env, github)
    return _review_draft(env, github, pr_number) if pr_number else 0


def _state_labels(issue: dict, state: str) -> list[str]:
    labels = [state]
    if any(label["name"] == PRIORITY_LABEL for label in issue.get("labels", [])):
        labels.append(PRIORITY_LABEL)
    return labels


def _apply_classification(github: GitHubClient, issue: dict, result: Classification) -> None:
    number = issue["number"]
    if result.action == "APPROVE":
        github.update_issue(number, title=result.title, body=result.description)
        github.set_labels(number, _state_labels(issue, TOPIC_LABEL))
    elif result.action == "REJECT":
        github.set_labels(number, [REJECTED_LABEL])
        github.comment(number, f"Propuesta descartada: {result.reason}")
        github.close(number)
    else:
        github.set_labels(number, _state_labels(issue, TRIAGE_LABEL))
        github.comment(number, f"Triaje automático pendiente de revisión: {result.reason}")


def triage_run(env: dict) -> int:
    github = GitHubClient(repo=env["GITHUB_REPOSITORY"], token=env["GITHUB_TOKEN"])
    if env.get("TRIAGE_ISSUE_NUMBER"):
        issue = github.get_issue(int(env["TRIAGE_ISSUE_NUMBER"]))
    else:
        issue = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))["issue"]
    config = load_config(env, "TRIAGE")
    try:
        result = classify(
            _client(config, "TRIAGE"), issue["title"], issue.get("body") or ""
        )
    except (LLMError, TriageError, OSError, ValueError, KeyError, TypeError) as exc:
        result = Classification("REVIEW", issue["title"], issue.get("body") or "", str(exc))
    _apply_classification(github, issue, result)
    print(f"Issue #{issue['number']}: {result.action.lower()} ({result.title}).")
    return 0


def main() -> None:
    sys.exit(run(dict(os.environ)))


def triage_main() -> None:
    sys.exit(triage_run(dict(os.environ)))
