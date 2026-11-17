ARG TASK_IMAGE=golang:1.26.9-bookworm@sha256:d9c68c2c51161e12fd77e4c6320687c9cd86e1af1e3ad6e6cd63ff970641453c
FROM ${TASK_IMAGE}
USER root
COPY tools/fuzz_runners/go /opt/fuzz-runners/go
RUN cd /opt/fuzz-runners/go \
 && GOTOOLCHAIN=local GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org go mod download \
 && go mod verify
ENV GOTOOLCHAIN=local
COPY tools/fuzz_runners /opt/fuzz-runners
WORKDIR /work
