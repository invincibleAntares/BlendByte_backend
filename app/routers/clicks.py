"""
Click tracking routes
"""
from fastapi import APIRouter, Depends, HTTPException

from app.auth import verify_clerk_token
from app.database import get_or_create_user, log_click, log_error
from app.models import ClickLogRequest, ClickLogResponse

router = APIRouter()


@router.post("/log", response_model=ClickLogResponse)
async def log_product_click(
    payload: ClickLogRequest,
    user_info: dict = Depends(verify_clerk_token),
):
    """
    Log a click for a product ASIN.
    """
    db_user_id = None
    try:
        db_user = await get_or_create_user(
            clerk_user_id=user_info["clerk_user_id"],
            email=user_info.get("email", ""),
            name=user_info.get("name", ""),
        )
        db_user_id = db_user.get("id")
        if not db_user_id:
            raise ValueError("Unable to resolve authenticated user")

        if not payload.product_asin:
            raise ValueError("product_asin is required")

        click_row = await log_click(
            user_id=db_user_id,
            product_asin=payload.product_asin,
            session_id=payload.session_id,
        )

        return ClickLogResponse(
            success=True,
            click_id=str(click_row.get("id", "")),
        )
    except HTTPException:
        raise
    except ValueError as exc:
        await log_error(
            error_message=str(exc),
            endpoint="/api/v1/clicks/log",
            user_id=db_user_id,
        )
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        await log_error(
            error_message=str(exc),
            endpoint="/api/v1/clicks/log",
            user_id=db_user_id,
        )
        raise HTTPException(status_code=500, detail="Failed to log click")
