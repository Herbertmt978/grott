ARG BUILD_FROM=python:3.11-slim
FROM ${BUILD_FROM}

ARG GROTT_REPO=https://github.com/Herbertmt978/grott.git
ARG GROTT_REF=Herb/auto-layout-ha-docker

ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch "${GROTT_REF}" "${GROTT_REPO}" /app
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY run.sh /run.sh
RUN chmod a+x /run.sh

EXPOSE 5279
CMD ["/run.sh"]
