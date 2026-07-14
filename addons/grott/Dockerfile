ARG PYTHON_IMAGE=python:3.11.15-alpine3.24@sha256:25976e9d34a0fab1f278cae931f34c8303d97bf0c0d7f85b6b4dcf641d7702a4
FROM ${PYTHON_IMAGE} AS builder

ENV PATH=/opt/venv/bin:$PATH \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apk add --no-cache build-base=0.5-r4
RUN python -m venv /opt/venv
COPY requirements.lock /build/requirements.lock
RUN python -m pip install --require-hashes -r /build/requirements.lock \
    && python -m pip check \
    && rm -rf \
        /opt/venv/lib/python3.11/site-packages/pip \
        /opt/venv/lib/python3.11/site-packages/pip-*.dist-info \
        /opt/venv/lib/python3.11/site-packages/setuptools \
        /opt/venv/lib/python3.11/site-packages/setuptools-*.dist-info \
        /opt/venv/lib/python3.11/site-packages/_distutils_hack \
        /opt/venv/lib/python3.11/site-packages/distutils-precedence.pth \
        /opt/venv/lib/python3.11/site-packages/pkg_resources \
    && rm -f /opt/venv/bin/pip /opt/venv/bin/pip3 /opt/venv/bin/pip3.11 /opt/venv/bin/wheel

FROM ${PYTHON_IMAGE} AS runtime

ARG BUILD_VERSION=dev
ARG VCS_REF=unknown

LABEL io.hass.version="${BUILD_VERSION}" \
      io.hass.type="app" \
      io.hass.arch="aarch64|amd64|armv7|i386" \
      org.opencontainers.image.source="https://github.com/Herbertmt978/grott" \
      org.opencontainers.image.description="Grott Home Assistant add-on with guarded Growatt layout selection" \
      org.opencontainers.image.version="${BUILD_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}"

ENV PATH=/opt/venv/bin:$PATH \
    HOME=/tmp \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

RUN apk add --no-cache tzdata=2026b-r0 su-exec=0.3-r0 \
    && addgroup -g 10001 -S grott \
    && adduser -D -H -u 10001 -S -G grott grott \
    && rm -rf \
        /usr/local/lib/python3.11/ensurepip \
        /usr/local/lib/python3.11/site-packages/pip \
        /usr/local/lib/python3.11/site-packages/pip-*.dist-info \
        /usr/local/lib/python3.11/site-packages/setuptools \
        /usr/local/lib/python3.11/site-packages/setuptools-*.dist-info \
        /usr/local/lib/python3.11/site-packages/wheel \
        /usr/local/lib/python3.11/site-packages/wheel-*.dist-info \
        /usr/local/lib/python3.11/site-packages/_distutils_hack \
        /usr/local/lib/python3.11/site-packages/distutils-precedence.pth \
        /usr/local/lib/python3.11/site-packages/pkg_resources \
    && rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.11 /usr/local/bin/wheel

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY grott.py grottconf.py grottdata.py grottlayout.py grottprotocol.py grottproxy.py grottserver.py grottsniffer.py /app/
COPY grottext /app/grottext
COPY examples/grott.ini /app/grott.ini
COPY ["examples/Home Assistent/grott_ha.py", "/app/grott_ha.py"]
COPY ["examples/Record Layout/", "/app/"]
COPY tools/container_healthcheck.py /usr/local/bin/container_healthcheck.py
COPY tools/validate_container_artifact.py /usr/local/bin/validate_container_artifact.py
COPY --chmod=0555 addons/grott/run.sh /run.sh

EXPOSE 5279
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD ["python", "/usr/local/bin/container_healthcheck.py"]
CMD ["/run.sh"]
