FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY pyproject.toml ./
RUN pip install --upgrade pip && \
    pip install --prefix=/install \
        anthropic httpx fastapi 'uvicorn[standard]' \
        pydantic pydantic-settings sentence-transformers numpy

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_DB_PATH=/app/app.db \
    AUDIT_DB_PATH=/app/audit.db \
    POLICY_FILE=/app/policy_and_faq.md \
    POLICY_INDEX_PATH=/app/policy_index.pkl \
    PORT=8080

WORKDIR /app
COPY --from=builder /install /usr/local
COPY agent/ /app/agent/
COPY service/ /app/service/
COPY audit/ /app/audit/
COPY scripts/ /app/scripts/
COPY app.db policy_and_faq.md /app/

# Pre-build the policy index at image build time so cold starts are fast.
RUN python -m scripts.build_policy_index

EXPOSE 8080
CMD ["sh", "-c", "uvicorn service.api:app --host 0.0.0.0 --port ${PORT}"]
