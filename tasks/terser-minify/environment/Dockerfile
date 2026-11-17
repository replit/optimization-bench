FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS payload-guard
COPY . /payload
RUN python3 /payload/payload_check.py --fetch /payload && touch -d @0 /payload-ready

FROM node@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c
COPY --from=payload-guard /payload-ready /payload-ready
COPY --from=payload-guard /payload/notices/ /app/notices/
RUN apt-get update && apt-get install -y --no-install-recommends python3 git ca-certificates && rm -rf /var/lib/apt/lists/*
COPY prepare.py source.lock.json /tmp/prepare/
RUN python3 /tmp/prepare/prepare.py && rm -rf /tmp/prepare
WORKDIR /app/deps
RUN cp /app/terser/package.json /app/terser/package-lock.json . \
 && npm ci --ignore-scripts --no-audit --no-fund
WORKDIR /app/terser
RUN export GIT_AUTHOR_NAME=optimbench GIT_AUTHOR_EMAIL=optimbench@invalid \
 GIT_COMMITTER_NAME=optimbench GIT_COMMITTER_EMAIL=optimbench@invalid \
 GIT_AUTHOR_DATE=2000-01-01T00:00:00+0000 GIT_COMMITTER_DATE=2000-01-01T00:00:00+0000 \
 && git -c init.defaultBranch=optimbench-baseline init -q \
 && git -c core.autocrlf=false -c core.filemode=true add -A \
 && git -c commit.gpgsign=false commit -qm "Terser 317f449 (pinned baseline)" \
 && test "$(git rev-parse HEAD^{tree})" = d198ecbf7e06ab0d95b5734a67a744c5376e1c39 \
 && test "$(git rev-parse HEAD)" = a9d69dac8d3025d823831d355766121b3b85c264 \
 && ln -s /app/deps/node_modules node_modules
ENV npm_config_offline=true npm_config_audit=false npm_config_fund=false
COPY --from=payload-guard /payload/workload/driver/ /app/workload/driver/
COPY --from=payload-guard /payload/workload/build.sh /payload/workload/runner.py /payload/workload/task.py /app/workload/
COPY --from=payload-guard /payload/workload/inputs/manifest.json /app/workload/inputs/manifest.json
RUN --network=none --mount=type=bind,from=payload-guard,source=/payload/workload/inputs,target=/app/workload/inputs \
 sh /app/workload/build.sh /app/baseline-build \
 && /app/baseline-build/run > /tmp/baseline.txt \
 && python3 -c "import json; m=json.load(open('/app/workload/inputs/manifest.json')); want={d['path']:(d['expect_len'],d['expect_sha']) for s in m['splits'].values() for b in s for d in b['docs']}; got={r[0]:(int(r[1]),r[2]) for r in (l.split() for l in open('/tmp/baseline.txt'))}; assert got==want" \
 && rm /tmp/baseline.txt \
 && dpkg-query -W > /app/os-packages.lock
WORKDIR /app
ENV UV_THREADPOOL_SIZE=8
