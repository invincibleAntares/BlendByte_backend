"""
Agent 5 - Personaliser
"""
import asyncio
import json
import os
import re
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from openai import APITimeoutError, APIConnectionError, AsyncOpenAI

from app.auth import verify_clerk_token
from app.database import get_or_create_user, log_error
from app.models import (
    Agent5PersonalizeRequest,
    Agent5PersonalizeResponse,
    PersonalizedProduct,
    RankedProduct,
)

router = APIRouter()

openai_api_key = os.getenv("OPENAI_API_KEY")
openai_client = AsyncOpenAI(api_key=openai_api_key) if openai_api_key else None
model_name = "gpt-4o-mini"
FALLBACK_REASON = "Matches their interests and occasion while staying practical for gifting."

SYSTEM_PROMPT = """
You are Agent 5 (Personaliser) for a gift recommendation app.
Given ranked products and a recipient profile, write one concise personalization reason per product.

Return STRICT JSON only in one of these equivalent formats:
1) {"reasons":[{"asin":"B0XXXX","reason":"..."}]}
2) {"B0XXXX":"...", "B0YYYY":"..."}

Rules:
- One reason per product asin.
- Each reason must be a single line, plain text.
- Keep each reason short (max ~140 chars).
- No markdown, no extra commentary.
""".strip()


def _to_single_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _extract_json_block(raw_text: str) -> str:
    text = raw_text.strip()
    if not text:
        return text

    if text.startswith("```"):
        fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if fence_match:
            return fence_match.group(1).strip()

    first_obj = text.find("{")
    last_obj = text.rfind("}")
    if first_obj != -1 and last_obj != -1 and first_obj < last_obj:
        return text[first_obj:last_obj + 1]

    first_arr = text.find("[")
    last_arr = text.rfind("]")
    if first_arr != -1 and last_arr != -1 and first_arr < last_arr:
        return text[first_arr:last_arr + 1]

    return text


def _parse_reasons_payload(parsed: Any) -> Dict[str, str]:
    reasons_by_asin: Dict[str, str] = {}

    def add_reason(asin: Any, reason: Any) -> None:
        if not isinstance(asin, str) or not isinstance(reason, str):
            return
        normalized_asin = asin.strip().upper()
        normalized_reason = _to_single_line(reason)
        if not normalized_asin or not normalized_reason:
            return
        reasons_by_asin[normalized_asin] = normalized_reason

    if isinstance(parsed, dict):
        if isinstance(parsed.get("reasons"), list):
            for item in parsed["reasons"]:
                if isinstance(item, dict):
                    add_reason(item.get("asin"), item.get("reason") or item.get("personalized_reason"))
        if isinstance(parsed.get("products"), list):
            for item in parsed["products"]:
                if isinstance(item, dict):
                    add_reason(item.get("asin"), item.get("reason") or item.get("personalized_reason"))

        for key, value in parsed.items():
            if key in {"reasons", "products"}:
                continue
            add_reason(key, value)
        return reasons_by_asin

    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                add_reason(item.get("asin"), item.get("reason") or item.get("personalized_reason"))
    return reasons_by_asin


def _safe_parse_reasons(raw_text: str) -> Dict[str, str]:
    extracted = _extract_json_block(raw_text)
    if not extracted:
        return {}
    try:
        parsed = json.loads(extracted)
    except json.JSONDecodeError:
        return {}
    return _parse_reasons_payload(parsed)


async def _generate_reasons(
    ranked_products: List[RankedProduct],
    recipient_profile: Dict[str, Any]
) -> Dict[str, str]:
    if not openai_client:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured")

    completion = None
    last_openai_error = None
    for attempt in range(2):
        try:
            completion = await openai_client.chat.completions.create(
                model=model_name,
                temperature=0.3,
                response_format={"type": "json_object"},
                timeout=30.0,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            "Recipient profile JSON:\n"
                            f"{json.dumps(recipient_profile, ensure_ascii=False)}\n\n"
                            "Ranked products JSON:\n"
                            f"{json.dumps([product.model_dump() for product in ranked_products], ensure_ascii=False)}\n\n"
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
    return _safe_parse_reasons(content)


@router.post("/personalize", response_model=Agent5PersonalizeResponse)
async def personalize_products(
    payload: Agent5PersonalizeRequest,
    user_info: dict = Depends(verify_clerk_token),
):
    """
    Add one-line personalized reasons for ranked products.
    """
    db_user_id = None
    try:
        db_user = await get_or_create_user(
            clerk_user_id=user_info["clerk_user_id"],
            email=user_info.get("email", ""),
            name=user_info.get("name", ""),
        )
        db_user_id = db_user.get("id")

        if not payload.ranked_products:
            raise HTTPException(status_code=400, detail="ranked_products must include at least one product")

        reasons_by_asin: Dict[str, str] = {}
        try:
            reasons_by_asin = await _generate_reasons(payload.ranked_products, payload.recipient_profile)
        except HTTPException:
            raise
        except Exception as llm_exc:
            await log_error(
                error_message=f"Agent5 LLM fallback: {str(llm_exc)}",
                endpoint="/api/v1/agent5/personalize",
                user_id=db_user_id,
            )
            reasons_by_asin = {}

        personalized_products: List[PersonalizedProduct] = []
        for product in payload.ranked_products:
            reason = reasons_by_asin.get(product.asin.strip().upper()) or FALLBACK_REASON
            personalized_products.append(
                PersonalizedProduct(
                    **product.model_dump(),
                    personalized_reason=_to_single_line(reason) or FALLBACK_REASON,
                )
            )

        return Agent5PersonalizeResponse(
            products=personalized_products,
            total_personalized=len(personalized_products),
        )
    except HTTPException:
        raise
    except ValueError as exc:
        await log_error(
            error_message=str(exc),
            endpoint="/api/v1/agent5/personalize",
            user_id=db_user_id,
        )
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        await log_error(
            error_message=str(exc),
            endpoint="/api/v1/agent5/personalize",
            user_id=db_user_id,
        )
        raise HTTPException(status_code=500, detail="Agent 5 failed to personalize products")
