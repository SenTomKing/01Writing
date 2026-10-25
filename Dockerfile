FROM python:3.13-slim AS api
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 WRITING_PROJECT_ROOT=/app
COPY pyproject.toml README.md ./
COPY src ./src
COPY backend ./backend
RUN pip install --no-cache-dir . && useradd --create-home writing
USER writing
EXPOSE 8000
CMD ["writing-web", "serve", "--host", "0.0.0.0"]
