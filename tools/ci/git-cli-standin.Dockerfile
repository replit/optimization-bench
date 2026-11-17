# CI stand-in for the git-history-change-summary task image, for the non-root
# CLI smoke only (tools/cli_nonroot_smoke.py). Same base image, the same
# build.sh (prefix=/root) and the same uid-10000 user as
# tasks/git-history-change-summary/environment/Dockerfile. It builds the pinned
# upstream tarball instead of the prepared baseline commit and skips payload
# staging: enough to reproduce root-prefix/permission failures, not to judge.
FROM rust:1.93.0-bookworm@sha256:d0a4aa3ca2e1088ac0c81690914a0d810f2eee188197034edf366ed010a2b382 AS rust-tools
FROM gcc@sha256:3ae15afe768b06d0c0fe088d822ba5f8045c26630bdacc8d8e7713cf5d8e7289
ENV DEBIAN_FRONTEND=noninteractive RUSTUP_HOME=/usr/local/rustup CARGO_HOME=/usr/local/cargo
ENV PATH=/usr/local/cargo/bin:$PATH
COPY --from=rust-tools /usr/local/rustup /usr/local/rustup
COPY --from=rust-tools /usr/local/cargo /usr/local/cargo
RUN apt-get update && apt-get install -y --no-install-recommends python3 libcurl4-openssl-dev libexpat1-dev libssl-dev zlib1g-dev gettext libpcre2-dev perl && rm -rf /var/lib/apt/lists/*
COPY git /app/git
COPY workload /app/workload
RUN sh /app/workload/build.sh /app/baseline-build && test "$(stat -c %a /root)" = 700
RUN groupadd --gid 10000 bench && useradd --uid 10000 --gid 10000 --home-dir /tmp bench
WORKDIR /app
