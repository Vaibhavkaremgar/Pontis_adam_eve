from fastapi import APIRouter

from app.api.routes.auth import router as auth_router
from app.api.routes.candidates import router as candidates_router
from app.api.routes.hiring import router as hiring_router
from app.api.routes.interviews import router as interviews_router
from app.api.routes.outreach import router as outreach_router
from app.api.routes.voice import router as voice_router

api_router = APIRouter(prefix="/api")
api_router.include_router(auth_router)
api_router.include_router(hiring_router)
api_router.include_router(candidates_router)
api_router.include_router(voice_router)
api_router.include_router(outreach_router)
api_router.include_router(interviews_router)

__all__ = ["api_router"]
