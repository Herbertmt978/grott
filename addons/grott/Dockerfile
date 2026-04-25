ARG BUILD_FROM=python:3.11-slim
FROM ${BUILD_FROM}

ARG BUILD_VERSION=0.1.4-beta
ARG GROTT_REPO=https://github.com/Herbertmt978/grott.git
ARG GROTT_REF=v0.1.4-beta

LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="aarch64|amd64|armv7|i386" \
    org.opencontainers.image.source="https://github.com/Herbertmt978/grott" \
    org.opencontainers.image.description="Grott Home Assistant add-on with guarded Growatt layout selection"

ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch "${GROTT_REF}" "${GROTT_REPO}" /app
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && apt-get purge -y --auto-remove build-essential git \
    && rm -rf /var/lib/apt/lists/*

COPY run.sh /run.sh
RUN chmod a+x /run.sh

EXPOSE 5279
CMD ["/run.sh"]
