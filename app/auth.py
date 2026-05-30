"""
Authentication utilities for Clerk JWT verification
"""
from fastapi import HTTPException, Request
import os
from typing import Optional, Dict
from clerk_backend_api import Clerk
from clerk_backend_api.security.types import AuthenticateRequestOptions

clerk_secret_key = os.getenv("CLERK_SECRET_KEY")
clerk_client = Clerk(bearer_auth=clerk_secret_key) if clerk_secret_key else None


def _safe_extract_name_from_claims(claims: Dict) -> str:
    full_name = (claims.get("name") or "").strip()
    if full_name:
        return full_name
    first_name = (claims.get("given_name") or "").strip()
    last_name = (claims.get("family_name") or "").strip()
    return f"{first_name} {last_name}".strip()


def _safe_extract_email_from_clerk_user(user: object) -> str:
    email_addresses = getattr(user, "email_addresses", None) or []
    if not email_addresses:
        return ""

    primary_email_id = getattr(user, "primary_email_address_id", None)
    if primary_email_id:
        for entry in email_addresses:
            if getattr(entry, "id", None) == primary_email_id:
                return (getattr(entry, "email_address", None) or "").strip()

    first_entry = email_addresses[0]
    return (getattr(first_entry, "email_address", None) or "").strip()

async def verify_clerk_token(
    request: Request
) -> dict:
    """
    Verify Clerk JWT token and extract user information
    
    Args:
        request: FastAPI request object
        
    Returns:
        dict: User information from JWT payload
        
    Raises:
        HTTPException: 401 if token is invalid or missing
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="Authorization header missing"
        )
    if not clerk_client or not clerk_secret_key:
        raise HTTPException(
            status_code=500,
            detail="Server auth misconfiguration"
        )

    try:
        request_state = clerk_client.authenticate_request(
            request=request,
            options=AuthenticateRequestOptions(secret_key=clerk_secret_key),
        )

        if not request_state.is_signed_in or not request_state.payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        claims = request_state.payload
        clerk_user_id = claims.get("sub")
        if not clerk_user_id:
            raise HTTPException(status_code=401, detail="Invalid token: missing user ID")

        user_info = {
            "clerk_user_id": clerk_user_id,
            "email": (claims.get("email") or "").strip(),
            "name": _safe_extract_name_from_claims(claims),
        }

        # Fallback to Clerk profile if token template does not include identity fields.
        if not user_info["email"] or not user_info["name"]:
            try:
                clerk_user = clerk_client.users.get(user_id=clerk_user_id)
                if not user_info["email"]:
                    user_info["email"] = _safe_extract_email_from_clerk_user(clerk_user)
                if not user_info["name"]:
                    first_name = (getattr(clerk_user, "first_name", None) or "").strip()
                    last_name = (getattr(clerk_user, "last_name", None) or "").strip()
                    user_info["name"] = f"{first_name} {last_name}".strip()
            except Exception:
                pass

        return user_info
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token"
        )

async def get_optional_user(
    request: Request
) -> Optional[dict]:
    """
    Get user info if token is present, return None if not
    Used for endpoints that work with or without authentication
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        return None
    
    try:
        return await verify_clerk_token(request)
    except HTTPException:
        return None
