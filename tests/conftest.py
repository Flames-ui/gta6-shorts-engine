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

import subprocess
import os

# Ensure sample test assets exist for fail-closed media tests
os.makedirs("/tmp/test_assets", exist_ok=True)
sample_vid = "/tmp/test_assets/valid_vertical_25s.mp4"
if not os.path.exists(sample_vid):
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=25:size=1080x1920:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=25",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        sample_vid
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

test_footage = "/tmp/test_assets/test_footage_1080p.mp4"
if not os.path.exists(test_footage):
    cmd2 = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=5:size=1920x1080:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=5",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        test_footage
    ]
    subprocess.run(cmd2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
