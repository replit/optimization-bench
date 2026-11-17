FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS payload-guard
COPY . /payload
RUN python3 /payload/payload_check.py --fetch /payload && touch -d @0 /payload-ready

FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS compiled
COPY --from=payload-guard /payload-ready /payload-ready
COPY --from=payload-guard /payload/notices/ /app/notices/
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ libc6-dev make git curl && rm -rf /var/lib/apt/lists/*

RUN curl -fL --retry 3 'https://sourceware.org/pub/bzip2/bzip2-1.0.8.tar.gz' -o /tmp/source.tar.gz && echo 'ab5a03176ee106d3f0fa90e381da478ddae405918153cca248e682cd0c4a2269  /tmp/source.tar.gz' | sha256sum -c - && mkdir -p /app/bzip2 && tar -xzf /tmp/source.tar.gz -C /app/bzip2 --strip-components=1 && rm /tmp/source.tar.gz
WORKDIR /app/bzip2
RUN export GIT_AUTHOR_NAME=optimbench GIT_AUTHOR_EMAIL=optimbench@invalid \
 GIT_COMMITTER_NAME=optimbench GIT_COMMITTER_EMAIL=optimbench@invalid \
 GIT_AUTHOR_DATE=2000-01-01T00:00:00+0000 GIT_COMMITTER_DATE=2000-01-01T00:00:00+0000 \
 && git -c init.defaultBranch=optimbench-baseline init -q \
 && git -c core.autocrlf=false -c core.filemode=true add -A \
 && git -c commit.gpgsign=false commit -qm 'bzip2-codec (pinned baseline)' \
 && test "$(git rev-parse HEAD^{tree})" = 5844823ad461bc44b84b8ce5cc9da79c2ac2048e \
 && test "$(git rev-parse HEAD)" = 9331e90397f09c942bc608770ed306c2c9168062
COPY --from=payload-guard /payload/workload/ /app/workload/
COPY prepare.py /app/prepare.py
ENV PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=8
RUN sh /app/workload/build.sh /app/baseline-build
WORKDIR /app

FROM compiled AS worker
COPY --from=payload-guard /payload-ready /payload-ready
COPY --from=payload-guard /payload/notices/ /app/notices/
RUN python3 /app/prepare.py /app/baseline-build && dpkg-query -W > /app/os-packages.lock
