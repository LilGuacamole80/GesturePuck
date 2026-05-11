FROM python:3.13-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY uv.lock .

RUN pip install uv

# Install deps but exclude desktop-only packages the server doesn't need
RUN uv sync --frozen --no-dev --no-install-package pynput --no-install-package evdev --no-install-package bleak

COPY . .

EXPOSE 5500

CMD ["uv", "run", "server.py"]