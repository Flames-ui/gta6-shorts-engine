"""Test SSRF protection and YouTube URL validation."""
import pytest
from shorts_generator.url_validation import validate_youtube_url


def test_valid_youtube_urls():
    valid = [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ&t=10s",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
    ]
    for url in valid:
        assert validate_youtube_url(url) == url


def test_rejected_unsafe_urls():
    unsafe = [
        "http://youtube.com/watch?v=123", # Plain HTTP
        "file:///etc/passwd",
        "file://localhost/test.mp4",
        "http://localhost:8000/internal",
        "http://169.254.169.254/latest/meta-data/",
        "https://attacker.com/exploit.mp4",
        "https://youtube.com@attacker.com/",
        "",
        "   ",
        "ftp://youtube.com/video",
    ]
    for url in unsafe:
        with pytest.raises(ValueError):
            validate_youtube_url(url)
