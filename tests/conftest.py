"""Test configuration and fixtures."""
import os
import pytest
from starlette.testclient import TestClient

from app.config import settings

# Explicitly configure settings for test suite
settings.ENVIRONMENT = "test"
settings.ENGINE_API_KEY = "test-secret-key-12345"
settings.LOCAL_OUTPUT_DIR = "/tmp/test_shorts_output"
settings.BUFFER_CLIENT_ID = "test_buffer_client_id"
settings.BUFFER_CLIENT_SECRET = "test_buffer_client_secret"
settings.BUFFER_REDIRECT_URI = "https://example.com/api/v1/publishing/buffer/callback"


from app.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers():
    return {"X-API-Key": "test-secret-key-12345"}
