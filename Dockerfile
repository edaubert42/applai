# The public demo hosted on Railway (railway.json, ADR 0007): applai demo, on the fictional
# candidate, with the built frontend. It holds no secret: the demo calls no model and no service.
# Railway builds main from its own GitHub link once CI passed (its "Wait for CI" setting): no
# workflow deploys, and GitHub holds no Railway token.

FROM node:24-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.10 /uv /usr/local/bin/uv
# Bytecode compiled at build time: a cold start does not compile it.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0 PATH=/app/.venv/bin:$PATH
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY . .
RUN uv sync --locked --no-dev
COPY --from=frontend /app/frontend/dist /app/frontend/dist
RUN useradd --system --no-create-home demo
USER demo
CMD ["applai", "demo"]
