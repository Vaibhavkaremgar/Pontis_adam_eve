import os
import importlib


os.environ.setdefault("DATABASE_URL", "sqlite:///./test_route_imports.db")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("PUBLIC_APP_URL", "http://localhost:3000")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")


def test_ats_route_imports_cleanly():
    module = importlib.import_module("app.api.routes.ats")
    assert hasattr(module, "router")
