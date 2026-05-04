from fastapi import APIRouter

from app.api.routes.auth import router as auth_router
from app.api.routes.company import router as company_router
from app.api.routes.ats import router as ats_router
from app.api.routes.candidates import router as candidates_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.hiring import router as hiring_router
from app.api.routes.interviews import router as interviews_router
from app.api.routes.outreach import router as outreach_router
from app.api.routes.recruiters import router as recruiters_router
from app.api.routes.replies import router as replies_router
from app.api.routes.voice import router as voice_router

api_router = APIRouter(prefix="/api")
api_router.include_router(auth_router)
api_router.include_router(company_router)
api_router.include_router(ats_router)
api_router.include_router(hiring_router)
api_router.include_router(jobs_router)
api_router.include_router(candidates_router)
api_router.include_router(voice_router)
api_router.include_router(outreach_router)
api_router.include_router(recruiters_router)
api_router.include_router(replies_router)
api_router.include_router(interviews_router)

__all__ = ["api_router"]
