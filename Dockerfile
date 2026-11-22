FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

# Install system dependencies: FFmpeg, ffprobe, subtitle/render fonts, OpenCV & GL runtimes, curl, git
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    fonts-freefont-ttf \
    fontconfig \
    libsm6 \
    libxext6 \
    libgl1 \
    libglib2.0-0 \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# Copy application source code
COPY . .

# Create output and temporary directories for video rendering and processing
RUN mkdir -p /app/output/clips /app/tmp && chmod -R 777 /app/output /app/tmp

EXPOSE 8000

# Default command starts FastAPI web service; Render worker overrides via dockerCommand
CMD ["bash", "app/start_combined.sh"]
