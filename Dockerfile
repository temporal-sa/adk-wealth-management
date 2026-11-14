# One image for the whole demo:
#   web (default command): the FastAPI API on port 8000, which also serves the built React app at /
#   worker:                python src/temporal_supervisor/run_worker.py
# Configuration comes from the environment: TEMPORAL_ADDRESS, TEMPORAL_NAMESPACE, TEMPORAL_API_KEY,
# TEMPORAL_TLS, TEMPORAL_TASK_QUEUE, REDIS_URL (or REDIS_HOST/REDIS_PORT/REDIS_PASSWORD),
# DEMO_DATA_STORE, GEMINI_API_KEY and GEMINI_MODEL.

# The built web app is static files, the same for every CPU: build it on the builder's own platform
FROM --platform=$BUILDPLATFORM node:26-alpine AS frontend
WORKDIR /frontend
COPY src/frontend/package.json src/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY src/frontend/ ./
RUN npm run build

FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY data/ data/
COPY src/ src/
COPY --from=frontend /frontend/build src/frontend/build
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONPATH=/app/src \
    FORWARDED_ALLOW_IPS=*
RUN useradd --uid 10001 --no-create-home app
USER 10001
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
