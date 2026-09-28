FROM python:3.12-slim
# ffmpeg: phone recordings (m4a/mp3) → 16 kHz mono WAV for the STT call
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY . .
ENV PORT=8080
CMD ["sh", "-c", "uv run uvicorn loop.web:app --host 0.0.0.0 --port ${PORT}"]
