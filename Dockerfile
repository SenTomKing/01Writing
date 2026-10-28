FROM python:3.13-slim@sha256:d98cc29d3266e535dd9da1099688d9cb3ca4753813571e071763d17725460962 AS api
COPY --from=ghcr.io/astral-sh/uv:0.9.17@sha256:5cb6b54d2bc3fe2eb9a8483db958a0b9eebf9edff68adedb369df8e7b98711a2 /uv /uvx /bin/
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 WRITING_PROJECT_ROOT=/app UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy UV_HTTP_TIMEOUT=120 UV_CONCURRENT_DOWNLOADS=2
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY backend ./backend
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable && useradd --create-home writing
ENV PATH="/app/.venv/bin:$PATH"
USER writing
EXPOSE 8000
CMD ["writing-web", "serve", "--host", "0.0.0.0"]
