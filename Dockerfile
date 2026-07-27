FROM python:3.11-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

FROM base AS deps
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

FROM base AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin
COPY app/ ./app/
COPY web/ ./web/
COPY scripts/ ./scripts/
COPY run_webapp.py ./
RUN useradd -m -u 1000 appuser \
    && mkdir -p /app/data \
    && chown -R appuser /app
USER appuser
# Bind to every interface INSIDE the container, or the published port maps to
# nothing: the health check below still passes (it runs from inside), so the
# container reports healthy while being unreachable. The app itself defaults to
# loopback everywhere else — see run_webapp.py.
ENV JOBFINDER_HOST=0.0.0.0
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1
CMD ["python", "run_webapp.py"]
