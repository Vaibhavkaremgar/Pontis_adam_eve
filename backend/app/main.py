from fastapi import FastAPI

from app.api.routes import api_router

app = FastAPI()


@app.get("/")
def home() -> dict[str, str]:
    return {"message": "Backend is running"}


app.include_router(api_router)
