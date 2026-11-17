FROM rust:1.93.0-bookworm@sha256:d0a4aa3ca2e1088ac0c81690914a0d810f2eee188197034edf366ed010a2b382 AS rust-tools
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS payload-guard
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
COPY payload_check.py payload-public.json /payload/
COPY workload/inputs /payload/workload/inputs
COPY notices /payload/notices
RUN python3 /payload/payload_check.py --fetch /payload && touch -d @0 /payload-ready
FROM gcc@sha256:3ae15afe768b06d0c0fe088d822ba5f8045c26630bdacc8d8e7713cf5d8e7289
ENV DEBIAN_FRONTEND=noninteractive RUSTUP_HOME=/usr/local/rustup CARGO_HOME=/usr/local/cargo
ENV PATH=/usr/local/cargo/bin:$PATH
COPY --from=rust-tools /usr/local/rustup /usr/local/rustup
COPY --from=rust-tools /usr/local/cargo /usr/local/cargo
RUN apt-get update && apt-get install -y --no-install-recommends python3 git ca-certificates pkg-config libcurl4-openssl-dev libexpat1-dev libssl-dev zlib1g-dev gettext libpcre2-dev perl && rm -rf /var/lib/apt/lists/*
COPY prepare.py source.lock.json layout.json /tmp/prepare/
RUN python3 /tmp/prepare/prepare.py && rm -rf /tmp/prepare
WORKDIR /app/git
RUN test "$(git symbolic-ref --short HEAD)" = optimbench-baseline \
 && test "$(git rev-parse HEAD)" = 92186d5b2a561afb5fe4b0dbce18a8906771faef \
 && test "$(git rev-parse HEAD^{tree})" = 79a1f663a05401ad749f6116a4898e03f2388323
COPY workload /app/workload
COPY --from=payload-guard /payload/workload/inputs /app/workload/inputs
COPY --from=payload-guard /payload/notices /app/notices
COPY --from=payload-guard /payload-ready /payload-ready
RUN sh /app/workload/build.sh /app/baseline-build \
 && /app/baseline-build/run > /tmp/baseline.json \
 && python3 /app/workload/check_public.py /tmp/baseline.json \
 && rm /tmp/baseline.json && dpkg-query -W > /app/os-packages.lock
RUN groupadd --gid 10000 bench && useradd --uid 10000 --gid 10000 --home-dir /tmp bench
WORKDIR /app
