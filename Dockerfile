# syntax=docker/dockerfile:1
FROM python:3.13-slim

# uv (gerenciador de pacotes) — copiado do binário oficial.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Instala dependências primeiro (cache de camada).
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

# Copia o código e instala o projeto no venv.
COPY src ./src
COPY data/seed ./data/seed
RUN uv sync --frozen --no-dev

EXPOSE 8000

# A API roda atrás do Caddy (TLS + domínio). Expõe só HTTP na 8000.
# Chamamos o uvicorn direto do venv (no PATH) — sem `uv run`, que re-sincronizaria
# o ambiente em runtime.
CMD ["uvicorn", "housing_radar.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
