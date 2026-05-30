"""
Database client and utilities for Supabase
"""
from supabase import create_client, Client
from datetime import datetime
import os
from typing import Optional, Dict, Any

# Initialize Supabase client
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not supabase_url or not supabase_key:
    raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")

supabase: Client = create_client(supabase_url, supabase_key)

async def get_or_create_user(clerk_user_id: str, email: str, name: str) -> Dict[str, Any]:
    """
    Get existing user or create new user in Supabase
    
    Args:
        clerk_user_id: Clerk user ID from JWT
        email: User email
        name: User name
        
    Returns:
        dict: User record from database
    """
    try:
        # Try to get existing user
        response = supabase.table("users").select("*").eq("clerk_user_id", clerk_user_id).execute()
        
        if response.data and len(response.data) > 0:
            # User exists, update last_login
            user = response.data[0]
            update_payload = {
                "last_login": datetime.utcnow().isoformat()
            }
            # Backfill/refresh identity fields when present from Clerk.
            if email:
                update_payload["email"] = email
            if name:
                update_payload["name"] = name

            updated = (
                supabase.table("users")
                .update(update_payload)
                .eq("id", user["id"])
                .execute()
            )

            if updated.data and len(updated.data) > 0:
                return updated.data[0]
            return user
        
        # User doesn't exist, create new
        new_user = {
            "clerk_user_id": clerk_user_id,
            "email": email,
            "name": name,
            "last_login": datetime.utcnow().isoformat(),
            "total_searches": 0
        }
        
        response = supabase.table("users").insert(new_user).execute()
        
        if response.data and len(response.data) > 0:
            return response.data[0]
        else:
            raise Exception("Failed to create user")
            
    except Exception as e:
        raise Exception(f"Database error: {str(e)}")

async def log_error(
    error_message: str,
    endpoint: str,
    user_id: Optional[str] = None
) -> None:
    """
    Log error to database
    
    Args:
        error_message: Error message to log
        endpoint: API endpoint where error occurred
        user_id: Optional user ID
    """
    try:
        supabase.table("logs").insert({
            "user_id": user_id,
            "error_message": error_message,
            "endpoint": endpoint,
            "created_at": datetime.utcnow().isoformat()
        }).execute()
    except Exception as e:
        # Don't raise exception for logging failures
        print(f"Failed to log error: {str(e)}")

async def increment_user_searches(user_id: str) -> None:
    """
    Increment total_searches counter for user
    
    Args:
        user_id: User ID (UUID from Supabase)
    """
    try:
        supabase.rpc("increment_searches", {"user_uuid": user_id}).execute()
    except Exception as e:
        print(f"Failed to increment search count: {str(e)}")
