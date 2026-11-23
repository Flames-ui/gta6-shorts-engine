"""YouTube retrieval policy: disabled by default, categorised failures, no access-control bypass."""
import pytest
import yt_dlp

from app.config import settings
from app.errors import RetrievalError
from app.media import youtube
from app.media.urls import parse_youtube_url

REF = parse_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")


def test_retrieval_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(settings, "YOUTUBE_RETRIEVAL_ENABLED", False)
    with pytest.raises(RetrievalError) as exc:
        youtube.ensure_enabled()
    assert exc.value.category == "retrieval_disabled"
    assert exc.value.details["category"] == "retrieval_disabled"


@pytest.mark.parametrize("message,category,retryable", [
    ("Private video. Sign in if you've been granted access to this video", "private", False),
    ("Video unavailable. This video is private", "private", False),
    ("Sign in to confirm you're not a bot", "access_restricted", False),
    ("This video is only available to Music Premium members", "members_only", False),
    ("The uploader has not made this video available in your country", "geo_restricted", False),
    ("Video unavailable", "unavailable", False),
    ("This live event will begin in a few minutes", "live_stream", False),
    ("HTTP Error 429: Too Many Requests", "rate_limited", True),
    ("timed out reading from server", "network_timeout", True),
    ("some completely unknown failure", "unknown_retrieval_error", False),
])
def test_yt_dlp_failures_are_categorised(message, category, retryable):
    assert youtube.classify_ytdlp_error(message) == (category, retryable)


def test_inspect_rejects_videos_over_the_duration_limit(monkeypatch):
    monkeypatch.setattr(settings, "YOUTUBE_RETRIEVAL_ENABLED", True)
    monkeypatch.setattr(settings, "MAX_SOURCE_DURATION_SECONDS", 600)

    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {"title": "Long", "duration": 7200, "channel": "C", "is_live": False, "availability": "public"}

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    with pytest.raises(RetrievalError) as exc:
        youtube.inspect(REF)
    assert exc.value.category == "duration_exceeds_limit"


def test_inspect_maps_download_errors_to_retrieval_errors(monkeypatch):
    monkeypatch.setattr(settings, "YOUTUBE_RETRIEVAL_ENABLED", True)

    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            raise yt_dlp.utils.DownloadError("ERROR: Video unavailable. This video is private")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    with pytest.raises(RetrievalError) as exc:
        youtube.inspect(REF)
    assert exc.value.category == "private"
    assert exc.value.retryable is False


def test_inspect_refuses_live_streams(monkeypatch):
    monkeypatch.setattr(settings, "YOUTUBE_RETRIEVAL_ENABLED", True)

    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {"title": "Live", "duration": 3600, "is_live": True, "availability": "public"}

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    with pytest.raises(RetrievalError) as exc:
        youtube.inspect(REF)
    assert exc.value.category == "live_stream"


def test_download_options_never_enable_cookies_or_bypass(monkeypatch):
    opts = youtube._ydl_options({})
    assert "cookiefile" not in opts and "cookiesfrombrowser" not in opts
    assert opts.get("noplaylist") is True
    assert "impersonate" not in str(opts).lower()
