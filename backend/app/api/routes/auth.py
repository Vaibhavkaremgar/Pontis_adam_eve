from uuid import uuid4

from fastapi import APIRouter

from app.schemas.user import LoginRequest, User

router = APIRouter(tags=["auth"])


@router.post("/auth/login", response_model=User)
def login(payload: LoginRequest) -> User:
    name = payload.email.split("@", 1)[0].replace(".", " ").title()
    return User(
        id=str(uuid4()),
        name=name,
        email=payload.email,
    )
