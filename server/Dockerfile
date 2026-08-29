FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app

# mssql-python's bundled ODBC driver needs these at runtime to load --
# https://github.com/microsoft/mssql-python#installation
RUN apt-get update && apt-get install -y --no-install-recommends \
    libltdl7 libkrb5-3 libgssapi-krb5-2 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY brewline/ ./brewline/
COPY server/main.py ./server/main.py
COPY mcp_server.py ./mcp_server.py
COPY data/ ./data/

EXPOSE 8000
EXPOSE 8765

CMD ["uv", "run", "gunicorn", "--bind", "0.0.0.0:8000", "--timeout", "120", "server.main:app"]
