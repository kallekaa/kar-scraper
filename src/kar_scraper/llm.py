from __future__ import annotations

import json
from typing import Any

from openai import OpenAI
from pydantic import ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential

from kar_scraper.config import Settings
from kar_scraper.models import KarCandidate, RankedCandidate, SearchIntent, json_schema_for


def _response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return str(output_text)

    output = getattr(response, "output", None) or []
    chunks: list[str] = []
    for item in output:
        if isinstance(item, dict):
            content = item.get("content", [])
        else:
            content = getattr(item, "content", None) or []
        for part in content:
            if isinstance(part, dict):
                text = part.get("text")
            else:
                text = getattr(part, "text", None)
            if text:
                chunks.append(str(text))
    return "\n".join(chunks)


def _load_json_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < start:
            raise
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object from the model")
    return value


class OpenAIPlanner:
    def __init__(self, settings: Settings) -> None:
        settings.require_openai_key()
        self._settings = settings
        self._client = OpenAI(api_key=settings.openai_api_key)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def interpret_request(self, request: str, max_results: int) -> SearchIntent:
        response = self._client.responses.create(
            model=self._settings.openai_model,
            instructions=(
                "Extract karaoke search intent from the user request. "
                "Return only JSON matching the schema. Keep unknown fields null. "
                "Set search_intent to a concise phrase useful for searching public .kar files."
            ),
            input=f"User request: {request}\nMaximum results: {max_results}",
            text={
                "format": {
                    "type": "json_schema",
                    "name": "kar_search_intent",
                    "schema": json_schema_for(SearchIntent),
                    "strict": True,
                }
            },
        )
        try:
            parsed = _load_json_object(_response_text(response))
            parsed["max_results"] = min(max_results, int(parsed.get("max_results") or max_results))
            return SearchIntent.model_validate(parsed)
        except (ValidationError, ValueError, json.JSONDecodeError):
            return fallback_intent(request, max_results)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def rank_candidates(
        self,
        request: str,
        intent: SearchIntent,
        candidates: list[KarCandidate],
        limit: int,
    ) -> list[RankedCandidate]:
        if not candidates:
            return []

        candidate_payload = [
            {
                "url": candidate.url,
                "source_page": candidate.source_page,
                "title": candidate.title,
            }
            for candidate in candidates[: min(len(candidates), 30)]
        ]
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "candidates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "url": {"type": "string"},
                            "accepted": {"type": "boolean"},
                            "score": {"type": "number", "minimum": 0, "maximum": 1},
                            "reason": {"type": "string"},
                        },
                        "required": ["url", "accepted", "score", "reason"],
                    },
                }
            },
            "required": ["candidates"],
        }
        response = self._client.responses.create(
            model=self._settings.openai_model,
            instructions=(
                "Rank direct .kar URL candidates for the user's karaoke request. "
                "Accept only candidates that plausibly match the requested artist, song, genre, or era. "
                "Reject weak or unrelated candidates. Return JSON only."
            ),
            input=json.dumps(
                {
                    "request": request,
                    "intent": intent.model_dump(),
                    "candidates": candidate_payload,
                    "limit": limit,
                },
                ensure_ascii=True,
            ),
            text={
                "format": {
                    "type": "json_schema",
                    "name": "kar_candidate_ranking",
                    "schema": schema,
                    "strict": True,
                }
            },
        )
        try:
            ranking = _load_json_object(_response_text(response)).get("candidates", [])
        except (ValueError, json.JSONDecodeError):
            ranking = []

        by_url = {candidate.url: candidate for candidate in candidates}
        ranked: list[RankedCandidate] = []
        seen: set[str] = set()
        for item in ranking:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", ""))
            original = by_url.get(url)
            if original is None or url in seen:
                continue
            seen.add(url)
            payload = original.model_dump()
            payload.update(
                {
                    "accepted": bool(item.get("accepted")),
                    "score": float(item.get("score") or 0),
                    "reason": str(item.get("reason") or original.reason),
                }
            )
            ranked.append(
                RankedCandidate(**payload)
            )

        if not ranked:
            ranked = [
                RankedCandidate(**(candidate.model_dump() | {"accepted": True, "score": 0.5}))
                for candidate in candidates
            ]

        ranked.sort(key=lambda candidate: candidate.score, reverse=True)
        return [candidate for candidate in ranked if candidate.accepted][:limit]


def fallback_intent(request: str, max_results: int) -> SearchIntent:
    return SearchIntent(search_intent=request.strip(), max_results=max_results)
