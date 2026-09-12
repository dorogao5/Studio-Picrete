FROM ghcr.io/astral-sh/uv:0.8.9 AS uv

FROM python:3.12-slim

ARG BUILD_REVISION=unknown
LABEL org.opencontainers.image.revision=$BUILD_REVISION
LABEL org.opencontainers.image.source="https://github.com/dorogao5/Studio-Picrete"
ENV BUILD_REVISION=$BUILD_REVISION

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"

COPY --from=uv /uv /usr/local/bin/uv
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/app ./app
COPY backend/scripts ./scripts
RUN uv sync --frozen --no-dev --no-editable --no-cache
COPY ops/release_physical_chemistry.py ./ops/release_physical_chemistry.py
COPY ops/apply_live_generation_corrections.py ./ops/apply_live_generation_corrections.py
COPY ops/content/physical_chemistry ./ops/content/physical_chemistry

COPY ops/check_general_inorganic.py ./ops/check_general_inorganic.py
COPY ops/release_general_inorganic.py ./ops/release_general_inorganic.py
COPY ops/content/general_inorganic ./ops/content/general_inorganic

EXPOSE 8100
CMD ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8100"]
