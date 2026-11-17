ARG TASK_IMAGE=rust:1.93.1-bookworm@sha256:7c4ae649a84014c467d79319bbf17ce2632ae8b8be123ac2fb2ea5be46823f31
FROM ${TASK_IMAGE}
USER root
COPY tools/fuzz_runners/rust /opt/fuzz-runners/rust
RUN rustup set auto-self-update disable \
 && CARGO_NET_OFFLINE=false cargo install cargo-fuzz --version 0.13.2 --locked \
 && echo '5acfd01930e49823e58c30dd8012d3338a620377d7c7d4cc140ca4b2169400e2  /usr/local/cargo/registry/cache/index.crates.io-1949cf8c6b5b557f/cargo-fuzz-0.13.2.crate' | sha256sum -c - \
 && cd /opt/fuzz-runners/rust && CARGO_NET_OFFLINE=false cargo fetch --locked \
 && rustup component add llvm-tools-preview \
 && curl -fLsS --max-time 120 https://static.rust-lang.org/dist/2026-10-01/channel-rust-nightly.toml -o /tmp/nightly-manifest.toml \
 && echo 'dd6769c70aab36f52d21f834e16e4991d2afe156c705d8a45f725bed8181446c  /tmp/nightly-manifest.toml' | sha256sum -c - \
 && rustup toolchain install nightly-2026-10-01 --profile minimal --component rust-src,llvm-tools-preview \
 && mv /tmp/nightly-manifest.toml /opt/fuzz-runners/nightly-manifest.toml \
 && chmod -R a+rX /usr/local/cargo /usr/local/rustup
# Production stable compiler stays the task's default; cargo-fuzz/sanitizers must
# explicitly select the separate pinned nightly, recorded in their receipts.
ENV CARGO_NET_OFFLINE=true CARGO_TERM_COLOR=never
COPY tools/fuzz_runners /opt/fuzz-runners
WORKDIR /work
