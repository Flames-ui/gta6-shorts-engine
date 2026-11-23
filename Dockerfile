# One image, two processes: the API (default) and the worker (`python -m app.worker`).
# FFmpeg/FFprobe (with libass for caption burn-in) are installed in the image.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/var/cache/huggingface

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN useradd --create-home --uid 10001 studio \
 && mkdir -p /var/data/studio-storage /tmp/studio /var/cache/huggingface \
 && chown -R studio:studio /var/data/studio-storage /tmp/studio /var/cache/huggingface /app

USER studio
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--timeout-graceful-shutdown", "30"]
