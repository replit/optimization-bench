# Optional matching-nightly standard-library + Miri overlay.
# Parent is the immutable Rust runner whose image receipt binds the task image.
ARG TASK_IMAGE
FROM ${TASK_IMAGE}
USER root
COPY tools/fuzz_runners/rust/checks /opt/fuzz-runners/rust/checks
RUN rustup component add miri --toolchain nightly-2026-10-01 \
 && cd /opt/fuzz-runners/rust/checks \
 && CARGO_NET_OFFLINE=false RUSTFLAGS="-Zsanitizer=thread" \
    cargo +nightly-2026-10-01 build --locked -Zbuild-std --target x86_64-unknown-linux-gnu \
 && CARGO_NET_OFFLINE=false cargo +nightly-2026-10-01 miri setup \
 && MIRI_ROOT="$(cargo +nightly-2026-10-01 miri setup --print-sysroot)" \
 && cp -a "$MIRI_ROOT" /opt/fuzz-miri-sysroot \
 && chmod -R a+rX /usr/local/cargo /usr/local/rustup /opt/fuzz-miri-sysroot \
 && rm -rf /opt/fuzz-runners/rust/checks/target
ENV MIRI_SYSROOT=/opt/fuzz-miri-sysroot
WORKDIR /work
