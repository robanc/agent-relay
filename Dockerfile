FROM python:3.11-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.9.13 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-cache

FROM python:3.11-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    RELAY_DATABASE_URL=sqlite:////data/agent-relay.db
WORKDIR /app
RUN groupadd --gid 10001 relay \
    && useradd --uid 10001 --gid relay --no-create-home --shell /usr/sbin/nologin relay \
    && mkdir /data && chown relay:relay /data
COPY --from=builder /app/.venv /app/.venv
COPY main.py database.py storage.py schemas.py errors.py dashboard.py dashboard.html ./
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3).close()"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
