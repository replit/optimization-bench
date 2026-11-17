FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS payload-guard
COPY . /payload
RUN --mount=type=cache,id=optimbench-serde-json-public-20f786e6bb2b4ff1c3c019f275cc41822d87da8c6b024ecb19f3010d0bd44a2c,target=/payload/workload/inputs/files,sharing=locked \
 python3 /payload/payload_check.py --fetch /payload && touch -d @0 /payload-ready
FROM rust:1.93.1-bookworm@sha256:7c4ae649a84014c467d79319bbf17ce2632ae8b8be123ac2fb2ea5be46823f31
COPY --from=payload-guard /payload-ready /payload-ready
COPY --from=payload-guard /payload/notices/ /app/notices/
COPY workload/ /app/workload/
RUN curl -fLsS https://codeload.github.com/serde-rs/json/tar.gz/20572a6d066958542d79cdd6cf1a7cd9f0c8e1f8 -o /tmp/source.tar.gz \
 && echo 'd638770aea490d6b9b94b67fa14c3533778431a8715792b2fac80e7ca405d721  /tmp/source.tar.gz' | sha256sum -c - \
 && mkdir /app/serde-json && tar -xzf /tmp/source.tar.gz -C /app/serde-json --strip-components=1 \
 && rm /tmp/source.tar.gz && cp /app/workload/driver/upstream.Cargo.lock /app/serde-json/Cargo.lock
WORKDIR /app/serde-json
RUN export GIT_AUTHOR_NAME=optimbench GIT_AUTHOR_EMAIL=optimbench@invalid GIT_COMMITTER_NAME=optimbench GIT_COMMITTER_EMAIL=optimbench@invalid \
 GIT_AUTHOR_DATE=2000-01-01T00:00:00+0000 GIT_COMMITTER_DATE=2000-01-01T00:00:00+0000 \
 && git -c init.defaultBranch=optimbench-baseline init -q \
 && git -c core.autocrlf=false -c core.filemode=true add -A && git add -f Cargo.lock \
 && git -c commit.gpgsign=false commit -qm 'serde_json1.0.154 plus protected offline test lock' \
 && test "$(git rev-parse HEAD^{tree})" = 5986ca6d571db1f4f733b78aa9428a4f8f86b73a && test "$(git rev-parse HEAD)" = 48ffa39cf6ce033c7fdcb985d68eae8dd4dc711c
RUN cargo fetch --locked && cd /app/workload/driver \
 && cargo fetch --locked --config 'patch.crates-io.serde_json.path="/app/serde-json"' \
 && cd /app/workload/driver/geojson && cargo fetch --locked --config 'patch.crates-io.serde_json.path="/app/serde-json"' \
 && chmod -R a+rX /usr/local/cargo/registry /usr/local/cargo/git /usr/local/rustup
ENV CARGO_NET_OFFLINE=true CARGO_TERM_COLOR=never
RUN --mount=type=cache,id=optimbench-serde-json-public-20f786e6bb2b4ff1c3c019f275cc41822d87da8c6b024ecb19f3010d0bd44a2c,target=/app/workload/inputs/files,sharing=locked,readonly \
 sh /app/workload/build.sh /app/baseline-build \
 && python3 /app/workload/check_public.py /app/baseline-build/prog
WORKDIR /app
