"""
Agent 2 - Prompt Builder
"""
import asyncio
import json
import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from openai import APITimeoutError, APIConnectionError, AsyncOpenAI

from app.auth import verify_clerk_token
from app.database import get_or_create_user, log_error
from app.models import Agent2BuildQueriesRequest, Agent2BuildQueriesResponse

router = APIRouter()

openai_api_key = os.getenv("OPENAI_API_KEY")
openai_client = AsyncOpenAI(api_key=openai_api_key) if openai_api_key else None
model_name = "gpt-4o-mini"

SYSTEM_PROMPT = """
You are Agent 2 (Prompt Builder) for a gift recommendation app.
Given a recipient profile JSON, generate Amazon.in search queries.

Return STRICT JSON only with:
{
  "queries": ["..."],
  "category_specificity": "specific" | "broad" | "unknown",
  "inferred_budget_inr": number | null
}

Rules:
1) Queries must be useful for Amazon.in product search.
2) If category is specific (e.g. watch, perfume, earbuds), generate focused queries.
3) If category is broad/unknown, generate diverse discovery queries.
4) No markdown, no explanation, no extra keys.
5) Keep queries concise and practical.
""".strip()


def _normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", query).strip()


def _extract_number_from_text(text: str) -> Optional[int]:
    digits = re.findall(r"\d[\d,]*", text)
    if not digits:
        return None
    raw = digits[0].replace(",", "")
    try:
        value = int(raw)
        return value if value > 0 else None
    except ValueError:
        return None


def _collect_budget_candidates(obj: Any, key_hint: str = "") -> Tuple[List[int], List[int]]:
    budget_key_candidates: List[int] = []
    other_candidates: List[int] = []

    if isinstance(obj, dict):
        for key, value in obj.items():
            b, o = _collect_budget_candidates(value, key)
            budget_key_candidates.extend(b)
            other_candidates.extend(o)
        return budget_key_candidates, other_candidates

    if isinstance(obj, list):
        for value in obj:
            b, o = _collect_budget_candidates(value, key_hint)
            budget_key_candidates.extend(b)
            other_candidates.extend(o)
        return budget_key_candidates, other_candidates

    value: Optional[int] = None
    if isinstance(obj, (int, float)):
        value = int(obj)
    elif isinstance(obj, str):
        value = _extract_number_from_text(obj)

    if value is None:
        return budget_key_candidates, other_candidates

    if value < 100 or value > 1_000_000:
        return budget_key_candidates, other_candidates

    is_budget_key = "budget" in key_hint.lower() or "price" in key_hint.lower()
    if is_budget_key:
        budget_key_candidates.append(value)
    else:
        other_candidates.append(value)

    return budget_key_candidates, other_candidates


def _extract_budget_from_profile(profile: Dict[str, Any]) -> Optional[int]:
    budget_candidates, _ = _collect_budget_candidates(profile)
    if budget_candidates:
        return budget_candidates[0]
    return None


def _safe_parse_agent_output(raw_text: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError("Model returned invalid JSON") from exc

    raw_queries = parsed.get("queries")
    category_specificity = (parsed.get("category_specificity") or "unknown").lower()
    inferred_budget = parsed.get("inferred_budget_inr")

    if not isinstance(raw_queries, list):
        raise ValueError("Model response missing queries list")

    cleaned_queries: List[str] = []
    seen = set()
    for raw_query in raw_queries:
        if not isinstance(raw_query, str):
            continue
        query = _normalize_query(raw_query)
        if len(query) < 3:
            continue
        query_key = query.lower()
        if query_key in seen:
            continue
        seen.add(query_key)
        cleaned_queries.append(query)

    if not cleaned_queries:
        raise ValueError("No valid queries generated")

    if category_specificity not in {"specific", "broad", "unknown"}:
        category_specificity = "unknown"

    if category_specificity == "specific":
        cleaned_queries = cleaned_queries[:2]
    else:
        cleaned_queries = cleaned_queries[:5]

    inferred_budget_value = None
    if isinstance(inferred_budget, (int, float)):
        inferred_budget_value = int(inferred_budget)
    elif isinstance(inferred_budget, str):
        inferred_budget_value = _extract_number_from_text(inferred_budget)

    return {
        "queries": cleaned_queries,
        "category_specificity": category_specificity,
        "inferred_budget_inr": inferred_budget_value,
    }


@router.post("/build-queries", response_model=Agent2BuildQueriesResponse)
async def build_queries(
    payload: Agent2BuildQueriesRequest,
    user_info: dict = Depends(verify_clerk_token),
):
    """
    Build Amazon search queries + budget band from recipient profile.
    """
    if not openai_client:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured")

    db_user_id = None
    try:
        db_user = await get_or_create_user(
            clerk_user_id=user_info["clerk_user_id"],
            email=user_info.get("email", ""),
            name=user_info.get("name", ""),
        )
        db_user_id = db_user.get("id")

        completion = None
        last_openai_error = None
        for attempt in range(2):
            try:
                completion = await openai_client.chat.completions.create(
                    model=model_name,
                    temperature=0.15,
                    response_format={"type": "json_object"},
                    timeout=30.0,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                "Recipient profile JSON:\n"
                                f"{json.dumps(payload.recipient_profile, ensure_ascii=False)}\n\n"
                                "Return JSON now."
                            ),
                        },
                    ],
                )
                break
            except (APITimeoutError, APIConnectionError) as exc:
                last_openai_error = exc
                if attempt == 0:
                    await asyncio.sleep(0.6)
                    continue
                raise

        if completion is None:
            raise ValueError(f"OpenAI call failed: {last_openai_error}")

        content = completion.choices[0].message.content or ""
        parsed = _safe_parse_agent_output(content)

        budget_stated = _extract_budget_from_profile(payload.recipient_profile)
        if not budget_stated:
            budget_stated = parsed.get("inferred_budget_inr")
        if not budget_stated or budget_stated < 100:
            raise ValueError("Could not determine a valid budget from recipient profile")

        if payload.force_expand_budget and budget_stated >= 100:
            budget_strategy = "expanded_30_percent"
            budget_searched = int(math.ceil(budget_stated * 1.3))
        elif budget_stated > 10_000:
            budget_strategy = "expanded_30_percent"
            budget_searched = int(math.ceil(budget_stated * 1.3))
        else:
            budget_strategy = "strict"
            budget_searched = int(budget_stated)

        return Agent2BuildQueriesResponse(
            queries=parsed["queries"],
            budget_stated=int(budget_stated),
            budget_searched=budget_searched,
            budget_strategy=budget_strategy,
            category_specificity=parsed["category_specificity"],
        )
    except HTTPException:
        raise
    except ValueError as exc:
        await log_error(
            error_message=str(exc),
            endpoint="/api/v1/agent2/build-queries",
            user_id=db_user_id,
        )
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        await log_error(
            error_message=str(exc),
            endpoint="/api/v1/agent2/build-queries",
            user_id=db_user_id,
        )
        raise HTTPException(status_code=500, detail="Agent 2 failed to build search queries")
