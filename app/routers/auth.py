"""
Authentication routes
"""
from fastapi import APIRouter, Depends, HTTPException
from app.auth import verify_clerk_token
from app.database import get_or_create_user, log_error
from app.models import VerifyTokenResponse, UserResponse

router = APIRouter()

@router.post("/verify", response_model=VerifyTokenResponse)
async def verify_token(user_info: dict = Depends(verify_clerk_token)):
    """
    Verify Clerk JWT token and create/update user in database
    
    This endpoint:
    1. Validates the JWT token from Authorization header
    2. Extracts user information from the token
    3. Creates user in database if first-time login
    4. Updates last_login timestamp if existing user
    5. Returns user data
    
    Headers:
        Authorization: Bearer <clerk_jwt_token>
        
    Returns:
        VerifyTokenResponse with user data
        
    Raises:
        401: Invalid or expired token
        500: Database error
    """
    try:
        # Get or create user in database
        user = await get_or_create_user(
            clerk_user_id=user_info["clerk_user_id"],
            email=user_info["email"],
            name=user_info["name"]
        )
        
        return VerifyTokenResponse(
            success=True,
            message="Token verified successfully",
            user=UserResponse(**user)
        )
        
    except Exception as e:
        # Log error to database
        await log_error(
            error_message=str(e),
            endpoint="/api/v1/auth/verify",
            user_id=None
        )
        
        raise HTTPException(
            status_code=500,
            detail=f"Failed to verify token: {str(e)}"
        )

@router.get("/me", response_model=UserResponse)
async def get_current_user(user_info: dict = Depends(verify_clerk_token)):
    """
    Get current authenticated user information
    
    Headers:
        Authorization: Bearer <clerk_jwt_token>
        
    Returns:
        UserResponse with current user data
        
    Raises:
        401: Invalid or expired token
    """
    try:
        user = await get_or_create_user(
            clerk_user_id=user_info["clerk_user_id"],
            email=user_info["email"],
            name=user_info["name"]
        )
        
        return UserResponse(**user)
        
    except Exception as e:
        await log_error(
            error_message=str(e),
            endpoint="/api/v1/auth/me",
            user_id=None
        )
        
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch user: {str(e)}"
        )
