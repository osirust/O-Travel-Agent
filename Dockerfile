FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70 AS builder
WORKDIR /build
COPY pyproject.toml requirements.lock ./
COPY otravel ./otravel
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -c requirements.lock .

FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70 AS runtime
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN useradd --system --uid 10001 --create-home app
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
USER app
EXPOSE 8080
CMD ["uvicorn", "otravel.api:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
