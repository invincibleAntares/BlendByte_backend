"""
Agent 4 - Product Ranker (pure math, no LLM)
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from app.auth import verify_clerk_token
from app.database import get_or_create_user, log_error
from app.models import Agent4RankRequest, Agent4RankResponse, RankedProduct, ScrapedProduct

router = APIRouter()


def _normalize_review_count(value: int, min_value: int, max_value: int) -> float:
    if max_value <= min_value:
        return 0.0
    return (value - min_value) / (max_value - min_value)


def _price_fit_score(price: float, budget: int) -> float:
    if budget <= 0:
        return 0.0

    distance_ratio = abs(price - budget) / budget
    base_score = max(0.0, 1.0 - distance_ratio)

    # Penalize above-budget products so in-budget items rank higher by default.
    if price > budget:
        base_score *= 0.65
    return min(1.0, base_score)


def _safe_rating(value: float | None) -> float:
    if value is None:
        return 0.0
    return max(0.0, min(5.0, value))


def _safe_reviews(value: int | None) -> int:
    if value is None:
        return 0
    return max(0, value)


def _safe_price(value: float | None, fallback_budget: int) -> float:
    if value is None:
        return float(fallback_budget)
    return max(0.0, value)


@router.post("/rank", response_model=Agent4RankResponse)
async def rank_products(
    payload: Agent4RankRequest,
    user_info: dict = Depends(verify_clerk_token),
):
    """
    Rank raw scraped products and return top 15.
    Formula:
      rating_score * 0.4 + review_score * 0.3 + price_fit_score * 0.3
    """
    db_user_id = None
    try:
        db_user = await get_or_create_user(
            clerk_user_id=user_info["clerk_user_id"],
            email=user_info.get("email", ""),
            name=user_info.get("name", ""),
        )
        db_user_id = db_user.get("id")

        if not payload.products:
            raise HTTPException(status_code=400, detail="No products provided for ranking")

        deduped: List[ScrapedProduct] = []
        seen_asins = set()
        for product in payload.products:
            asin = product.asin.strip().upper()
            if not asin or asin in seen_asins:
                continue
            seen_asins.add(asin)
            deduped.append(product)

        if not deduped:
            raise HTTPException(status_code=400, detail="No valid products provided for ranking")

        review_values = [_safe_reviews(product.review_count) for product in deduped]
        min_reviews = min(review_values)
        max_reviews = max(review_values)

        ranked: List[RankedProduct] = []
        for product in deduped:
            rating = _safe_rating(product.rating)
            rating_score = rating / 5.0

            reviews = _safe_reviews(product.review_count)
            review_score = _normalize_review_count(reviews, min_reviews, max_reviews)

            price = _safe_price(product.price, payload.budget_searched)
            price_fit = _price_fit_score(price, payload.budget_searched)

            final_score = (rating_score * 0.4) + (review_score * 0.3) + (price_fit * 0.3)

            ranked.append(
                RankedProduct(
                    **product.model_dump(),
                    final_score=round(final_score, 4),
                    rating_score=round(rating_score, 4),
                    review_score=round(review_score, 4),
                    price_fit_score=round(price_fit, 4),
                )
            )

        ranked.sort(
            key=lambda item: (
                item.final_score,
                item.rating or 0.0,
                item.review_count or 0,
            ),
            reverse=True,
        )

        top_ranked = ranked[:15]
        return Agent4RankResponse(
            ranked_products=top_ranked,
            total_ranked=len(top_ranked),
        )
    except HTTPException:
        raise
    except Exception as exc:
        await log_error(
            error_message=str(exc),
            endpoint="/api/v1/agent4/rank",
            user_id=db_user_id,
        )
        raise HTTPException(status_code=500, detail="Agent 4 failed to rank products")
