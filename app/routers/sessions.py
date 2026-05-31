"""
Search sessions routes
"""
from fastapi import APIRouter, Depends, HTTPException

from app.auth import verify_clerk_token
from app.database import (
    create_search_session,
    get_or_create_user,
    get_search_session,
    increment_user_searches,
    list_search_sessions,
    log_error,
)
from app.models import SessionCreateRequest, SessionListResponse, SessionResponse

router = APIRouter()


@router.post("", response_model=SessionResponse)
async def create_session(
    payload: SessionCreateRequest,
    user_info: dict = Depends(verify_clerk_token),
):
    """
    Save completed search session for current user.
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

        session_row = await create_search_session(
            user_id=db_user_id,
            recipient_profile=payload.recipient_profile,
            search_queries=payload.search_queries,
            products_returned=payload.products_returned,
            budget_stated=payload.budget_stated,
            budget_searched=payload.budget_searched,
        )
        await increment_user_searches(db_user_id)
        return SessionResponse(**session_row)
    except HTTPException:
        raise
    except ValueError as exc:
        await log_error(
            error_message=str(exc),
            endpoint="/api/v1/sessions",
            user_id=db_user_id,
        )
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        await log_error(
            error_message=str(exc),
            endpoint="/api/v1/sessions",
            user_id=db_user_id,
        )
        raise HTTPException(status_code=500, detail="Failed to save session")


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    user_info: dict = Depends(verify_clerk_token),
):
    """
    List completed search sessions newest first.
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

        sessions = await list_search_sessions(db_user_id)
        return SessionListResponse(
            sessions=[SessionResponse(**item) for item in sessions],
            total=len(sessions),
        )
    except HTTPException:
        raise
    except ValueError as exc:
        await log_error(
            error_message=str(exc),
            endpoint="/api/v1/sessions",
            user_id=db_user_id,
        )
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        await log_error(
            error_message=str(exc),
            endpoint="/api/v1/sessions",
            user_id=db_user_id,
        )
        raise HTTPException(status_code=500, detail="Failed to list sessions")


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    user_info: dict = Depends(verify_clerk_token),
):
    """
    Fetch a single completed search session with ownership check.
    """
    db_user_id = None
    try:
        normalized_session_id = session_id.strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")

        db_user = await get_or_create_user(
            clerk_user_id=user_info["clerk_user_id"],
            email=user_info.get("email", ""),
            name=user_info.get("name", ""),
        )
        db_user_id = db_user.get("id")
        if not db_user_id:
            raise ValueError("Unable to resolve authenticated user")

        session = await get_search_session(db_user_id, normalized_session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return SessionResponse(**session)
    except HTTPException:
        raise
    except ValueError as exc:
        await log_error(
            error_message=str(exc),
            endpoint=f"/api/v1/sessions/{session_id}",
            user_id=db_user_id,
        )
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        await log_error(
            error_message=str(exc),
            endpoint=f"/api/v1/sessions/{session_id}",
            user_id=db_user_id,
        )
        raise HTTPException(status_code=500, detail="Failed to fetch session")
