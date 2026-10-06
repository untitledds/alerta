# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm AS builder

ARG BUILD_DATE
ARG BUILD_NUMBER
ARG RELEASE
ARG VERSION

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libldap2-dev \
        libpq-dev \
        libsasl2-dev \
        libxml2-dev \
        libxslt1-dev \
        python3-dev \
        xmlsec1 \
        libxmlsec1-dev \
        pkg-config \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY requirements.txt /build/requirements.txt

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip build wheel

RUN --mount=type=cache,target=/root/.cache/pip \
    pip wheel --wheel-dir /wheels -r /build/requirements.txt

COPY . /build

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m build --wheel --outdir /wheels .


FROM python:3.12-slim-bookworm AS runtime

ARG BUILD_DATE
ARG BUILD_NUMBER
ARG RELEASE
ARG VERSION

LABEL org.opencontainers.image.title="alerta-api" \
      org.opencontainers.image.description="Alerta API" \
      org.opencontainers.image.created=$BUILD_DATE \
      org.opencontainers.image.url="https://github.com/untitledds/alerta/pkgs/container/alerta-api" \
      org.opencontainers.image.source="https://github.com/untitledds/alerta" \
      org.opencontainers.image.version=$RELEASE \
      org.opencontainers.image.revision=$VERSION \
      org.opencontainers.image.licenses=Apache-2.0

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    ALERTA_ENDPOINT=http://localhost:8080 \
    FLASK_SKIP_DOTENV=1 \
    # --- plugin bootstrap settings ---
    BOOT_PLUGINS="" \
    PLUGINS_FILE="" \
    PLUGINS_DIR="/home/alerta/.local" \
    BOOTSTRAP_MARKER="/app/.plugins_bootstrapped" \
    HOME="/home/alerta"

RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
        libpq5 \
        libldap-2.5-0 \
        libsasl2-2 \
        libxml2 \
        libxslt1.1 \
        xmlsec1 \
        postgresql-client \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r alerta && \
       useradd -r -g alerta -m -d /home/alerta -s /sbin/nologin alerta

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY --from=builder /build/requirements.txt /tmp/requirements.txt

RUN pip install --no-index --find-links=/wheels -r /tmp/requirements.txt alerta-server \
    && rm -rf /wheels /tmp/requirements.txt

RUN SITE_PACKAGES=$(python -c "import site; print(site.getsitepackages()[0])") && \
    printf "BUILD_NUMBER = '%s'\nBUILD_DATE = '%s'\nBUILD_VCS_NUMBER = '%s'\n" \
        "$BUILD_NUMBER" "$BUILD_DATE" "$VERSION" > "${SITE_PACKAGES}/alerta/build.py"


COPY supervisord.conf /app/supervisord.conf

COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh && \
    chown -R alerta:alerta /app /home/alerta

USER alerta

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8080/management/healthcheck || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["supervisord", "-c", "/app/supervisord.conf"]
