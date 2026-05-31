"""
Agent 3 - Amazon.in Scraper with Redis cache
"""
import json
import os
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, HTTPException

from app.auth import verify_clerk_token
from app.database import get_or_create_user, log_error
from app.models import Agent3ScrapeRequest, Agent3ScrapeResponse, ScrapedProduct

router = APIRouter()

AMAZON_SEARCH_URL = "https://www.amazon.in/s?k={query}"
CACHE_TTL_SECONDS = 7200
MAX_PRODUCTS_PER_QUERY = 15
MAX_CACHE_VALUE_LENGTH = 6000

UPSTASH_REDIS_URL = os.getenv("UPSTASH_REDIS_URL", "").rstrip("/")
UPSTASH_REDIS_TOKEN = os.getenv("UPSTASH_REDIS_TOKEN", "")
AMAZON_AFFILIATE_TAG = os.getenv("AMAZON_AFFILIATE_TAG", "")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


class CaptchaDetectedError(Exception):
    pass


ASIN_REGEX = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})")


def _normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", query).strip()


def _cache_key(query: str, budget: int) -> str:
    normalized = _normalize_query(query).lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    return f"amazon:{normalized}:{budget}"


def _is_rest_upstash_url(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")


def _build_affiliate_url(asin: str) -> str:
    base = f"https://www.amazon.in/dp/{asin}"
    if AMAZON_AFFILIATE_TAG:
        return f"{base}?tag={quote_plus(AMAZON_AFFILIATE_TAG)}"
    return base


def _is_captcha_page(html: str) -> bool:
    text = html.lower()
    return (
        "enter the characters you see below" in text
        or "type the characters you see in this image" in text
        or "captcha" in text
    )


def _parse_price(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    cleaned = text.replace("₹", "").replace(",", "").strip()
    match = re.search(r"\d+(?:\.\d+)?", cleaned)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _parse_rating(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _parse_review_count(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    cleaned = text.replace(",", "").strip()
    match = re.search(r"\d+", cleaned)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def _first_text(node: Any, selectors: List[str]) -> Optional[str]:
    for selector in selectors:
        found = node.select_one(selector)
        if not found:
            continue
        value = found.get_text(strip=True)
        if value:
            return value
    return None


def _extract_asin(node: Any) -> Optional[str]:
    direct_asin = (node.get("data-asin") or "").strip()
    if re.fullmatch(r"[A-Z0-9]{10}", direct_asin):
        return direct_asin

    link_node = node.select_one("h2 a[href]") or node.select_one("a.a-link-normal[href]")
    if not link_node:
        return None

    href = link_node.get("href") or ""
    match = ASIN_REGEX.search(href)
    if not match:
        return None
    return match.group(1)


def _extract_image_url(node: Any) -> Optional[str]:
    image_node = node.select_one("img.s-image")
    if not image_node:
        return None

    for attr in ["src", "data-src", "data-image-source-density-high"]:
        value = image_node.get(attr)
        if isinstance(value, str) and value.strip():
            return value.strip()

    srcset = image_node.get("srcset")
    if isinstance(srcset, str) and srcset.strip():
        first_src = srcset.split(",")[0].strip().split(" ")[0]
        if first_src:
            return first_src
    return None


def _extract_products_from_html(html: str, source_query: str) -> List[ScrapedProduct]:
    soup = BeautifulSoup(html, "lxml")
    nodes = soup.select('[data-component-type="s-search-result"]')

    products: List[ScrapedProduct] = []
    seen_asins = set()

    for node in nodes:
        asin = _extract_asin(node)
        if not asin or asin in seen_asins:
            continue

        name = (_first_text(node, ["h2 a span", "h2 span", "a.a-link-normal.s-line-clamp-2"]) or "").strip()
        if not name:
            continue

        price_text = _first_text(
            node,
            [
                "span.a-price > span.a-offscreen",
                ".a-price .a-offscreen",
                "span[data-a-color='price'] span.a-offscreen",
            ],
        )

        rating_text = _first_text(
            node,
            [
                "span.a-icon-alt",
                "i.a-icon-star-small span.a-icon-alt",
            ],
        )

        review_text = _first_text(
            node,
            [
                "span.a-size-base.s-underline-text",
                "span[aria-label*='ratings']",
                "a[href*='customerReviews'] span.a-size-base",
            ],
        )

        image_url = _extract_image_url(node)

        product = ScrapedProduct(
            asin=asin,
            name=name,
            price=_parse_price(price_text),
            rating=_parse_rating(rating_text),
            review_count=_parse_review_count(review_text),
            image_url=image_url,
            affiliate_url=_build_affiliate_url(asin),
            source_query=source_query,
        )

        products.append(product)
        seen_asins.add(asin)

        if len(products) >= MAX_PRODUCTS_PER_QUERY:
            break

    return products


async def _cache_get_json(key: str) -> Optional[Any]:
    if not (_is_rest_upstash_url(UPSTASH_REDIS_URL) and UPSTASH_REDIS_TOKEN):
        return None
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(
                f"{UPSTASH_REDIS_URL}/get/{quote_plus(key)}",
                headers={"Authorization": f"Bearer {UPSTASH_REDIS_TOKEN}"},
            )
            if response.status_code >= 400:
                return None
            payload = response.json()
            raw = payload.get("result")
            if not raw:
                return None
            return json.loads(raw)
    except Exception:
        return None


async def _cache_set_json(key: str, value: Any, ttl_seconds: int = CACHE_TTL_SECONDS) -> None:
    if not (_is_rest_upstash_url(UPSTASH_REDIS_URL) and UPSTASH_REDIS_TOKEN):
        return
    try:
        serialized = json.dumps(value, ensure_ascii=False)
        # Upstash REST command-in-path can fail on very long URLs.
        # Skip caching oversized payloads instead of failing the request path.
        if len(serialized) > MAX_CACHE_VALUE_LENGTH:
            return
        encoded_value = quote_plus(serialized)
        async with httpx.AsyncClient(timeout=8.0) as client:
            await client.post(
                f"{UPSTASH_REDIS_URL}/set/{quote_plus(key)}/{encoded_value}",
                params={"EX": ttl_seconds},
                headers={"Authorization": f"Bearer {UPSTASH_REDIS_TOKEN}"},
            )
    except Exception:
        return


async def _fetch_search_html(query: str, attempt: int) -> str:
    url = AMAZON_SEARCH_URL.format(query=quote_plus(query))
    headers = {
        "User-Agent": USER_AGENTS[attempt % len(USER_AGENTS)],
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection": "keep-alive",
    }
    async with httpx.AsyncClient(timeout=18.0, follow_redirects=True) as client:
        response = await client.get(url, headers=headers)
    if response.status_code >= 400:
        raise ValueError(f"Amazon request failed with status {response.status_code}")
    html = response.text
    if _is_captcha_page(html):
        raise CaptchaDetectedError("Captcha detected")
    return html


async def _scrape_query_products(query: str) -> List[ScrapedProduct]:
    last_captcha_error: Optional[Exception] = None
    for attempt in range(2):
        try:
            html = await _fetch_search_html(query, attempt)
            return _extract_products_from_html(html, source_query=query)
        except CaptchaDetectedError as exc:
            last_captcha_error = exc
            if attempt == 0:
                continue
            break
    if last_captcha_error:
        raise CaptchaDetectedError("Having trouble fetching results, please try again")
    return []


@router.post("/scrape", response_model=Agent3ScrapeResponse)
async def scrape_products(
    payload: Agent3ScrapeRequest,
    user_info: dict = Depends(verify_clerk_token),
):
    """
    Scrape Amazon.in product results for generated queries with Redis cache.
    """
    db_user_id = None
    try:
        db_user = await get_or_create_user(
            clerk_user_id=user_info["clerk_user_id"],
            email=user_info.get("email", ""),
            name=user_info.get("name", ""),
        )
        db_user_id = db_user.get("id")

        deduped_products: List[ScrapedProduct] = []
        seen_asins = set()
        query_warnings: List[str] = []
        captcha_failures = 0

        normalized_queries: List[str] = []
        seen_queries = set()
        for raw_query in payload.queries:
            normalized = _normalize_query(raw_query)
            if not normalized:
                continue
            query_key = normalized.lower()
            if query_key in seen_queries:
                continue
            seen_queries.add(query_key)
            normalized_queries.append(normalized)

        if not normalized_queries:
            raise HTTPException(status_code=400, detail="No valid queries provided")

        for query in normalized_queries:
            key = _cache_key(query, payload.budget_searched)
            cached = await _cache_get_json(key)

            query_products: List[ScrapedProduct]
            if isinstance(cached, list):
                query_products = []
                for item in cached:
                    if not isinstance(item, dict):
                        continue
                    try:
                        query_products.append(ScrapedProduct(**item))
                    except Exception:
                        continue
            else:
                try:
                    query_products = await _scrape_query_products(query)
                except CaptchaDetectedError:
                    captcha_failures += 1
                    query_warnings.append(f"Captcha hit on query: {query}")
                    continue
                except Exception:
                    query_warnings.append(f"Failed to fetch query: {query}")
                    continue
                await _cache_set_json(
                    key,
                    [product.model_dump() for product in query_products],
                    CACHE_TTL_SECONDS,
                )

            for product in query_products:
                if product.asin in seen_asins:
                    continue
                seen_asins.add(product.asin)
                deduped_products.append(product)

        if len(deduped_products) == 0 and captcha_failures > 0:
            raise HTTPException(
                status_code=503,
                detail="Having trouble fetching results, please try again",
            )

        warning_parts: List[str] = []
        if 0 < len(deduped_products) < 3:
            warning_parts.append(
                "We found limited options for this search, try adjusting your budget or category."
            )
        if query_warnings:
            warning_parts.append("Some queries could not be fetched right now.")

        warning_message = " ".join(warning_parts) if warning_parts else None

        return Agent3ScrapeResponse(
            products=deduped_products,
            total_products=len(deduped_products),
            warning_message=warning_message,
        )
    except HTTPException:
        raise
    except Exception as exc:
        await log_error(
            error_message=str(exc),
            endpoint="/api/v1/agent3/scrape",
            user_id=db_user_id,
        )
        raise HTTPException(
            status_code=500,
            detail="Agent 3 failed to fetch product results",
        )
