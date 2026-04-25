from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request

from .models import Paper
from .network import open_url


DEFAULT_MODELS = {
    "openai": "gpt-4.1-mini",
    "anthropic": "claude-3-5-sonnet-latest",
}


def generate_daily_summary(
    *,
    provider: str,
    api_key: str,
    model: str,
    papers: list[Paper],
    daily_days: int,
) -> str:
    if not papers:
        return "No new papers were found in the current daily window, so there is nothing to summarize yet."

    provider_name = provider.strip().lower()
    if provider_name not in DEFAULT_MODELS:
        raise ValueError("Choose either OpenAI or Anthropic as the AI provider.")
    if not api_key.strip():
        raise ValueError(f"Enter a {provider_name.title()} API key first.")
    if not model.strip():
        raise ValueError("Enter a model name before generating the summary.")

    prompt = _build_prompt(papers, daily_days)
    if provider_name == "openai":
        return _call_openai(api_key=api_key.strip(), model=model.strip(), prompt=prompt)
    return _call_anthropic(api_key=api_key.strip(), model=model.strip(), prompt=prompt)


def _build_prompt(papers: list[Paper], daily_days: int) -> str:
    lines = [
        "Create a daily research secretary summary for an arXiv watchlist.",
        f"Time window: last {daily_days} day(s).",
        "",
        "Write the summary with these sections:",
        "1. Snapshot",
        "2. What changed today",
        "3. Papers worth opening first",
        "4. Patterns across the feed",
        "",
        "Keep it concise, practical, and specific. Mention paper titles directly.",
        "Focus on why each paper matters for the watchlist rather than rewriting abstracts.",
        "If there are many similar papers, group them briefly.",
        "",
        "Papers:",
    ]
    for index, paper in enumerate(papers[:30], start=1):
        lines.extend(
            [
                f"{index}. {paper.title}",
                f"Published: {paper.published}",
                f"Authors: {', '.join(paper.authors) or 'Unknown'}",
                f"Matched watches: {', '.join(sorted(paper.matched_watch_labels)) or 'None'}",
                f"Primary category: {paper.primary_category or 'Unspecified'}",
                f"Abstract: {paper.summary or 'No abstract available.'}",
                "",
            ]
        )
    return "\n".join(lines)


def _call_openai(*, api_key: str, model: str, prompt: str) -> str:
    payload = {
        "model": model,
        "temperature": 0.3,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an expert arXiv research secretary. Summarize new papers clearly, "
                    "highlight what is novel, and help the user decide what to read first."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }
    request = Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    response = _read_json_response(request)
    choices = response.get("choices", [])
    if not choices:
        raise ValueError("OpenAI returned no summary choices.")
    message = choices[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, str) and content.strip():
        return content.strip()
    raise ValueError("OpenAI returned an empty summary.")


def _call_anthropic(*, api_key: str, model: str, prompt: str) -> str:
    payload = {
        "model": model,
        "max_tokens": 1200,
        "system": (
            "You are an expert arXiv research secretary. Summarize new papers clearly, "
            "highlight what is novel, and help the user decide what to read first."
        ),
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}],
            }
        ],
    }
    request = Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    response = _read_json_response(request)
    parts = response.get("content", [])
    text_parts = [part.get("text", "").strip() for part in parts if part.get("type") == "text"]
    summary = "\n\n".join(part for part in text_parts if part)
    if summary:
        return summary
    raise ValueError("Anthropic returned an empty summary.")


def _read_json_response(request: Request) -> dict:
    try:
        with open_url(request, timeout=60) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(_extract_error_message(detail) or f"HTTP {exc.code} from AI provider.") from exc
    except URLError as exc:
        raise ValueError(f"Could not reach the AI provider: {exc.reason}") from exc

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("The AI provider returned a response that was not valid JSON.") from exc
    if not isinstance(data, dict):
        raise ValueError("The AI provider returned an unexpected response shape.")
    return data


def _extract_error_message(payload: str) -> str:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return payload.strip()
    if not isinstance(data, dict):
        return payload.strip()

    error = data.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        type_name = error.get("type")
        if isinstance(type_name, str) and type_name.strip():
            return type_name.strip()
    if isinstance(error, str) and error.strip():
        return error.strip()
    return payload.strip()
