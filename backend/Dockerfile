FROM ghcr.io/astral-sh/uv:0.8.9 AS uv

FROM python:3.12-slim

ARG BUILD_REVISION=unknown
LABEL org.opencontainers.image.revision=$BUILD_REVISION
LABEL org.opencontainers.image.source="https://github.com/dorogao5/Studio-Picrete"
ENV BUILD_REVISION=$BUILD_REVISION

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"

COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
COPY app ./app
COPY scripts ./scripts
RUN uv sync --frozen --no-dev --no-editable --no-cache

EXPOSE 8100
CMD ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8100"]
