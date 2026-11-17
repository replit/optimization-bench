ARG TASK_IMAGE=python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
FROM ${TASK_IMAGE}
# Immutable Debian snapshot pins the complete compiler/runtime dependency closure.
# This compiler is for *instrumented* native builds, never production/timing.
USER root
RUN . /etc/os-release && if [ "$ID" != debian ] || [ "${VERSION_CODENAME:-}" != bookworm ]; then \
      echo 'Unsupported native reference overlay parent: requires Debian bookworm; preserve the task distro/toolchain and use a task-owned compatible instrumented recipe.' >&2; exit 1; fi
RUN rm -f /etc/apt/sources.list /etc/apt/sources.list.d/* \
 && printf '%s\n' \
 'deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/20261025T000000Z bookworm main' \
 'deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/20261025T000000Z bookworm-updates main' \
 'deb [check-valid-until=no] https://snapshot.debian.org/archive/debian-security/20261025T000000Z bookworm-security main' > /etc/apt/sources.list \
 && apt-get -o Acquire::Check-Valid-Until=false update \
 && apt-get install -y --no-install-recommends ca-certificates clang-16 llvm-16 llvm-16-dev libclang-rt-16-dev afl++ build-essential cmake ninja-build python3 git \
 && dpkg-query -W > /opt/fuzz-os-packages.lock \
 && rm -rf /var/lib/apt/lists/*
ENV CC=clang-16 CXX=clang++-16 LLVM_COV=llvm-cov-16 LLVM_PROFDATA=llvm-profdata-16
COPY tools/fuzz_runners /opt/fuzz-runners
WORKDIR /work
