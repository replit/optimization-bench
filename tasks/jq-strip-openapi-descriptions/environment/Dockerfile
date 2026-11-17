FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS payload-guard
RUN --mount=type=bind,target=/payload,readonly \
 python3 /payload/payload_check.py /payload && touch -d @0 /payload-ready

FROM ubuntu@sha256:c098726dd9b9733f3e885f8de0ef375206a311b0a317eadc40d347b1115fb944
COPY --from=payload-guard /payload-ready /payload-ready
COPY notices/ /app/notices/
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends python3 git ca-certificates build-essential autoconf automake libtool bison flex pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
COPY prepare.py source.lock.json layout.json /tmp/prepare/
RUN python3 /tmp/prepare/prepare.py && rm -rf /tmp/prepare
WORKDIR /app/jq
RUN export GIT_AUTHOR_NAME=optimbench GIT_AUTHOR_EMAIL=optimbench@invalid \
 GIT_COMMITTER_NAME=optimbench GIT_COMMITTER_EMAIL=optimbench@invalid \
 GIT_AUTHOR_DATE=2000-01-01T00:00:00+0000 GIT_COMMITTER_DATE=2000-01-01T00:00:00+0000 \
 && git -c core.autocrlf=false -c core.filemode=true status --porcelain \
 && test "$(git rev-parse HEAD^{tree})" = 23051d36b6275fe949c81dfa45943f50bb43804d \
 && test "$(git rev-parse HEAD)" = 9008a2517ab2361d286612b4a9164212764f9ddd
COPY workload/*.py workload/*.sh workload/layout.json /app/workload/
COPY workload/native/ /app/workload/native/
COPY workload/notices/ /app/workload/notices/
COPY workload/inputs/manifest.json /app/workload/inputs/manifest.json
RUN --mount=type=bind,source=workload/inputs/files,target=/app/workload/inputs/files,readonly \
 sh /app/workload/build.sh /app/baseline-build \
 && /app/baseline-build/run > /tmp/baseline.txt \
 && python3 /app/workload/check_public.py /tmp/baseline.txt \
 && rm /tmp/baseline.txt \
 && dpkg-query -W > /app/os-packages.lock
RUN groupadd --gid 10000 bench && useradd --uid 10000 --gid 10000 --home-dir /tmp bench
WORKDIR /app
ENV GOMAXPROCS=8 OMP_NUM_THREADS=8
