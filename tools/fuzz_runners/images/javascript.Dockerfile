ARG TASK_IMAGE=node@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c
FROM python@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS certificates
FROM ${TASK_IMAGE}
USER root
COPY --from=certificates /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
RUN rm -f /etc/apt/sources.list /etc/apt/sources.list.d/* \
 && printf '%s\n' \
 'deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/20261025T000000Z bookworm main' \
 'deb [check-valid-until=no] https://snapshot.debian.org/archive/debian-security/20261025T000000Z bookworm-security main' > /etc/apt/sources.list \
 && apt-get -o Acquire::Check-Valid-Until=false update \
 && apt-get install -y --no-install-recommends python3 \
 && dpkg-query -W > /opt/fuzz-os-packages.lock && rm -rf /var/lib/apt/lists/*
COPY tools/fuzz_runners/package*.json /opt/fuzz-runners/
RUN cd /opt/fuzz-runners && npm_config_offline=false npm ci --no-audit --no-fund
ENV NODE_PATH=/opt/fuzz-runners/node_modules npm_config_offline=true
COPY tools/fuzz_runners /opt/fuzz-runners
WORKDIR /work
