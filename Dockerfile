FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt


FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app app

COPY --from=builder /opt/venv /opt/venv

COPY --chown=10001:10001 . .

COPY entrypoint.sh /app/entrypoint.sh
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && python -m playwright install --with-deps chromium \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && chmod +x /app/entrypoint.sh \
    && mkdir -p /app/uploads \
    && chown app:app /app/uploads \
    && chown -R app:app /opt/ms-playwright

USER 10001:10001

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
