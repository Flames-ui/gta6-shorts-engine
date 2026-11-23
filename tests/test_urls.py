import pytest

from app.errors import StudioError
from app.media.urls import parse_youtube_url

ID = "dQw4w9WgXcQ"

VALID = [
    (f"https://www.youtube.com/watch?v={ID}", ID),
    (f"https://youtube.com/watch?v={ID}&t=42s&feature=share", ID),
    (f"https://m.youtube.com/watch?v={ID}", ID),
    (f"https://youtu.be/{ID}", ID),
    (f"https://youtu.be/{ID}?si=abc123", ID),
    (f"https://www.youtube.com/shorts/{ID}", ID),
    (f"https://www.youtube.com/embed/{ID}", ID),
    (f"https://www.youtube-nocookie.com/embed/{ID}", ID),
    (f"https://www.youtube.com/live/{ID}", ID),
    (f"  https://www.youtube.com/watch?v={ID}  ", ID),
]


@pytest.mark.parametrize("raw,video_id", VALID)
def test_supported_formats_normalise_to_one_canonical_url(raw, video_id):
    ref = parse_youtube_url(raw)
    assert ref.video_id == video_id
    assert ref.normalized_url == f"https://www.youtube.com/watch?v={video_id}"


BAD = [
    "",
    "not a url",
    f"http://www.youtube.com/watch?v={ID}",          # plain http is refused
    f"ftp://www.youtube.com/watch?v={ID}",
    f"javascript:alert({ID})",
    "file:///etc/passwd",
    f"https://evil.example.com/watch?v={ID}",       # unsupported host
    f"https://youtube.com.evil.example.com/watch?v={ID}",
    f"https://user:pass@www.youtube.com/watch?v={ID}",  # credentials in URL
    f"https://www.youtube.com:8443/watch?v={ID}",   # non-default port
    "https://www.youtube.com/watch?v=short",       # id length
    f"https://www.youtube.com/watch?v={ID}!!",      # invalid characters
    "https://www.youtube.com/playlist?list=PL123",  # not a single video
    "https://localhost/watch?v=" + ID,
    "https://169.254.169.254/latest/meta-data",     # SSRF target
    "https://127.0.0.1/watch?v=" + ID,
    "https://youtu.be/",
    "https://www.youtube.com/watch?v=" + "a" * 3000,  # too long
]


@pytest.mark.parametrize("raw", BAD)
def test_unsupported_or_unsafe_urls_are_rejected(raw):
    with pytest.raises(StudioError) as exc:
        parse_youtube_url(raw)
    assert exc.value.status_code == 422
    assert exc.value.code in {"invalid_youtube_url", "unsupported_host", "unsupported_url", "url_too_long"}


def test_error_message_never_echoes_the_raw_input():
    secret_like = "https://evil.example.com/?token=SUPERSECRET123"
    with pytest.raises(StudioError) as exc:
        parse_youtube_url(secret_like)
    assert "SUPERSECRET123" not in exc.value.message


def test_surrounding_whitespace_from_pasting_is_tolerated():
    assert parse_youtube_url(f"https://www.youtube.com/watch?v={ID}\n").video_id == ID
