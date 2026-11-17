FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS payload-guard
RUN --mount=type=bind,source=.,target=/payload,readonly \
    python3 /payload/payload_check.py /payload && touch -d @0 /payload-ready

# Worker environment for optimbench/sqlfmt-cli: the pinned sqlfmt source (8c676b5) with its modules
# downloaded, Go 1.26.9, the protected standalone driver, and a prebuilt baseline. Public input bodies
# use transient read-only mounts, not image layers. Network is used only for the pinned build.
FROM golang:1.26.9-bookworm@sha256:d9c68c2c51161e12fd77e4c6320687c9cd86e1af1e3ad6e6cd63ff970641453c
COPY --from=payload-guard /payload-ready /payload-ready
COPY notices/ /app/notices/

ARG SQLFMT_COMMIT=8c676b51ea62e6ce33221e0d9d6d8a43256c942c
ARG SQLFMT_ARCHIVE_SHA256=2fb1eec6dc5cf3eea27e72c7a6d00340520df1464a15c9f406d87ff0e8af9f37
ENV CGO_ENABLED=0 GOTOOLCHAIN=local GOFLAGS=-mod=mod
RUN apt-get update && apt-get install -y --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://codeload.github.com/dimitri/sqlfmt/tar.gz/${SQLFMT_COMMIT}" -o /tmp/sqlfmt.tgz \
 && echo "${SQLFMT_ARCHIVE_SHA256}  /tmp/sqlfmt.tgz" | sha256sum -c - \
 && mkdir -p /app/sqlfmt && tar -xzf /tmp/sqlfmt.tgz -C /app/sqlfmt --strip-components=1 && rm /tmp/sqlfmt.tgz
WORKDIR /app/sqlfmt
RUN export GIT_AUTHOR_NAME=optimbench GIT_AUTHOR_EMAIL=optimbench@invalid \
 GIT_COMMITTER_NAME=optimbench GIT_COMMITTER_EMAIL=optimbench@invalid \
 GIT_AUTHOR_DATE=2000-01-01T00:00:00+0000 GIT_COMMITTER_DATE=2000-01-01T00:00:00+0000 \
 && git -c init.defaultBranch=optimbench-baseline init -q \
 && git -c core.autocrlf=false -c core.filemode=true add -A \
 && git -c commit.gpgsign=false commit -qm "sqlfmt 8c676b5 (pinned baseline)" \
 && test "$(git rev-parse HEAD^{tree})" = da6c989b02299d77d374e7dec598677a5a5751c2 \
 && test "$(git rev-parse HEAD)" = b92aa1e57ceb8d875328eadc9ab4f47b05285705

COPY workload/*.py workload/*.sh /app/workload/
COPY workload/native/*.go workload/native/*.py /app/workload/native/
COPY workload/inputs/*.json /app/workload/inputs/
RUN go mod download
ENV GOPROXY=off GOSUMDB=off
RUN --mount=type=bind,source=workload/inputs/files,target=/app/workload/inputs/files,readonly \
    chmod 755 /app/workload/build.sh \
 && sh /app/workload/build.sh /app/baseline-build \
 && python3 /app/workload/check_public.py /app/baseline-build
WORKDIR /app
