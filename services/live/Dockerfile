# aira-live (Cloud Run). Built in GitHub Actions: docker build -t <registry>/aira/aira-live:<sha> services/live
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PATH="/app/.venv/bin:$PATH" \
    PORT=8080

WORKDIR /app

# Dependencies first (cached layer), from the lockfile only.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

# The service code (i18n JSON catalogs live inside the package).
COPY aira_live ./aira_live

RUN useradd --system --uid 10001 aira
USER aira

EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn aira_live.main:app --host 0.0.0.0 --port ${PORT} --ws-ping-interval 20"]
