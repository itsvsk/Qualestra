FROM python:3.14-slim
ARG SENTINELQA_VERSION=0.1.0
ARG SENTINELQA_REVISION=unknown
ARG SENTINELQA_SOURCE=https://github.com/itsvsk/Qualestra
LABEL org.opencontainers.image.title="Qualestra worker" \
      org.opencontainers.image.description="Evidence-first supervised autonomous QA worker and control API" \
      org.opencontainers.image.version="${SENTINELQA_VERSION}" \
      org.opencontainers.image.revision="${SENTINELQA_REVISION}" \
      org.opencontainers.image.source="${SENTINELQA_SOURCE}" \
      org.opencontainers.image.licenses="Apache-2.0"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv==0.11.29 && uv sync --locked --no-dev --no-install-project
RUN playwright install --with-deps chromium firefox webkit
COPY README.md ./
COPY sentinelqa sentinelqa
COPY apps apps
COPY atlas atlas
COPY config config
COPY contracts contracts
RUN uv sync --locked --no-dev
COPY . .
RUN useradd --create-home sentinel \
    && mkdir -p /artifacts /home/sentinel/.cache \
    && mv /root/.cache/ms-playwright /home/sentinel/.cache/ms-playwright \
    && chown -R sentinel:sentinel /app /artifacts /home/sentinel/.cache
USER sentinel
CMD ["python", "-m", "apps.qa_worker.main"]
