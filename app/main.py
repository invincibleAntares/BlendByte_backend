"""
BlendByte FastAPI Application
Main entry point for the backend API
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from contextlib import asynccontextmanager
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from app.routers import auth, agent1, agent2, agent3, agent4, agent5, clicks, sessions

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    # Startup
    print("🚀 BlendByte API starting up...")
    print(f"Environment: {os.getenv('ENVIRONMENT', 'development')}")
    yield
    # Shutdown
    print("👋 BlendByte API shutting down...")

# Initialize FastAPI app
app = FastAPI(
    title="BlendByte API",
    description="AI-powered gift recommendation platform",
    version="1.0.0",
    lifespan=lifespan
)

# CORS Configuration
allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Authentication"])
app.include_router(agent1.router, prefix="/api/v1/agent1", tags=["Agent 1"])
app.include_router(agent2.router, prefix="/api/v1/agent2", tags=["Agent 2"])
app.include_router(agent3.router, prefix="/api/v1/agent3", tags=["Agent 3"])
app.include_router(agent4.router, prefix="/api/v1/agent4", tags=["Agent 4"])
app.include_router(agent5.router, prefix="/api/v1/agent5", tags=["Agent 5"])
app.include_router(clicks.router, prefix="/api/v1/clicks", tags=["Clicks"])
app.include_router(sessions.router, prefix="/api/v1/sessions", tags=["Sessions"])


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Normalize request validation failures to clear 400 responses."""
    return JSONResponse(
        status_code=400,
        content={
            "detail": "Invalid request payload",
            "errors": exc.errors(),
        },
    )

@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "BlendByte API",
        "version": "1.0.0"
    }

@app.get("/health")
async def health_check():
    """Detailed health check"""
    return {
        "status": "healthy",
        "environment": os.getenv("ENVIRONMENT", "development"),
        "api_version": "v1"
    }
