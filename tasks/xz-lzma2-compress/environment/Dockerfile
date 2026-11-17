FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS payload-guard
COPY . /payload
RUN python3 /payload/payload_check.py --fetch /payload && touch -d @0 /payload-ready

FROM docker.io/library/ubuntu@sha256:42527649dbc4f9197a6073f426f488cbff0e9cc9f2afce32d91c383e4875634a
COPY --from=payload-guard /payload-ready /payload-ready
COPY --from=payload-guard /payload/notices/ /app/notices/
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends python3 git ca-certificates build-essential cmake ninja-build && rm -rf /var/lib/apt/lists/*
COPY prepare.py source.lock.json layout.json /tmp/prepare/
RUN python3 /tmp/prepare/prepare.py && rm -rf /tmp/prepare
WORKDIR /app/xz
RUN export GIT_AUTHOR_NAME=optimbench GIT_AUTHOR_EMAIL=optimbench@invalid \
 GIT_COMMITTER_NAME=optimbench GIT_COMMITTER_EMAIL=optimbench@invalid \
 GIT_AUTHOR_DATE=2000-01-01T00:00:00+0000 GIT_COMMITTER_DATE=2000-01-01T00:00:00+0000 \
 && git -c core.autocrlf=false -c core.filemode=true status --porcelain \
 && test "$(git rev-parse HEAD^{tree})" = d8ab9627f367fbb86b19aea806d66647ff6a26cd \
 && test "$(git rev-parse HEAD)" = b08d2f70929593905d133761d639dd29bc9df2b2
COPY --from=payload-guard /payload/workload/ /app/workload/
RUN sh /app/workload/build.sh /app/baseline-build \
 && /app/baseline-build/run > /tmp/baseline.txt \
 && python3 /app/workload/check_public.py /tmp/baseline.txt \
 && rm /tmp/baseline.txt \
 && dpkg-query -W > /app/os-packages.lock
RUN groupadd --gid 10000 bench && useradd --uid 10000 --gid 10000 --home-dir /tmp bench
WORKDIR /app
ENV GOMAXPROCS=8 OMP_NUM_THREADS=8
