FROM ghcr.io/astral-sh/uv:0.10.9@sha256:10902f58a1606787602f303954cea099626a4adb02acbac4c69920fe9d278f82 AS uv
FROM python:3.13.12-slim-bookworm@sha256:a58daefb915e1e03ad48f3ca4df8832065412c5c35cacb9d39f4229184de12b6 AS dependencies
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy
WORKDIR /build
COPY pyproject.toml uv.lock ./
# Source is copied directly below: no unpinned Hatchling/build-isolation step.
# Require wheels; fail explicitly if the locked set cannot support the target platform.
RUN uv sync --frozen --no-dev --no-install-project --no-build --no-cache

FROM python:3.13.12-slim-bookworm@sha256:a58daefb915e1e03ad48f3ca4df8832065412c5c35cacb9d39f4229184de12b6 AS runtime
ENV PATH="/opt/venv/bin:$PATH" PYTHONPATH="/app/src:/app" \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=dependencies /opt/venv /opt/venv
COPY src/ ./src/
USER 10001:10001
STOPSIGNAL SIGTERM
ENTRYPOINT ["python", "-m", "uvicorn"]

FROM runtime AS mock
COPY mock_service/ ./mock_service/
EXPOSE 8001
CMD ["mock_service.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1", "--limit-concurrency", "32", "--timeout-graceful-shutdown", "15"]

FROM runtime AS proxy
COPY scripts/verify.py scripts/load_generator.py ./scripts/
EXPOSE 8000
CMD ["pokeproxy.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--limit-concurrency", "32", "--timeout-graceful-shutdown", "30"]
