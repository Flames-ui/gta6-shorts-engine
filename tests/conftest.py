"""Test configuration and fixtures."""
import os
import pytest
from starlette.testclient import TestClient

from app.config import settings

# Explicitly configure settings for test suite
settings.ENVIRONMENT = "test"
settings.ENGINE_API_KEY = "test-secret-key-12345"
settings.LOCAL_OUTPUT_DIR = "/tmp/test_shorts_output"

from app.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers():
    return {"X-API-Key": "test-secret-key-12345"}
