FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS payload-guard
COPY . /payload
RUN python3 /payload/payload_check.py --fetch /payload && touch -d @0 /payload-ready

FROM node@sha256:363e1587494626837fa7f9a23bdb453d13b0ff3c67c705c2805cfc69c2d2fad7
COPY --from=payload-guard /payload-ready /payload-ready
COPY --from=payload-guard /payload/notices/ /app/notices/
ENV PATH=/toolchain/bin:/usr/local/bin:/usr/bin:/bin CI=1 INSTA_UPDATE=no RAYON_NUM_THREADS=8 LANG=C.UTF-8
COPY prepare.py source.lock.json public-workloads.lock.json /tmp/prepare/
COPY workload/public-trees.json /tmp/prepare/public-trees.json
RUN python3 /tmp/prepare/prepare.py source /app/ruff \
 && python3 /tmp/prepare/prepare.py rust /toolchain \
 && python3 /tmp/prepare/prepare.py projects /app/projects \
 && rm -rf /tmp/prepare
WORKDIR /app/ruff
# Cargo.lock fixes every registry crate and checksum; cargo vendor verifies those checksums.
RUN CARGO_HOME=/tmp/cargo cargo vendor --locked /assets/vendor > /tmp/vendor-config \
 && rm -rf /tmp/cargo /tmp/vendor-config \
 && export GIT_AUTHOR_NAME=optimbench GIT_AUTHOR_EMAIL=optimbench@invalid \
 GIT_COMMITTER_NAME=optimbench GIT_COMMITTER_EMAIL=optimbench@invalid \
 GIT_AUTHOR_DATE=2000-01-01T00:00:00+0000 GIT_COMMITTER_DATE=2000-01-01T00:00:00+0000 \
 && git -c init.defaultBranch=optimbench-baseline init -q \
 && git -c core.autocrlf=false -c core.filemode=true add -A \
 && git -c commit.gpgsign=false commit -qm "Ruff 3265ed1 (pinned baseline)" \
 && test "$(git rev-parse HEAD^{tree})" = fcd093243ceb39862ffb0db3685faa4b113acf81 \
 && test "$(git rev-parse HEAD)" = 16b6a6ad9804d3d982862be0710e829e72baf381
COPY workload/build.sh /app/workload/build.sh
RUN chmod 755 /app/workload/build.sh \
 && sh /app/workload/build.sh /app/baseline-build
COPY workload/inputs /app/workload/inputs
COPY workload/runner.py workload/task.py /app/workload/
RUN /app/baseline-build/run > /tmp/baseline.txt \
 && python3 -c "import json,sys; m=json.load(open('/app/workload/inputs/manifest.json')); want={d['path']:(d['expect_len'],d['expect_sha']) for s in m['splits'].values() for b in s for d in b['docs']}; got={r[0]:(int(r[1]),r[2]) for r in (l.split() for l in open('/tmp/baseline.txt'))}; sys.exit(got!=want)" \
 && rm /tmp/baseline.txt && dpkg-query -W > /app/os-packages.lock
RUN mkdir -p /app/notices/upstream \
 && cp /app/ruff/LICENSE /app/notices/upstream/ruff-LICENSE \
 && cp /app/projects/pandas/LICENSE /app/notices/upstream/pandas-LICENSE \
 && cp /app/projects/fastapi/LICENSE /app/notices/upstream/fastapi-LICENSE
RUN /usr/sbin/groupadd --gid 10000 bench \
 && /usr/sbin/useradd --uid 10000 --gid 10000 --home-dir /home/bench --create-home bench \
 && chmod -R a+rX /assets/vendor /toolchain /app/projects \
 && chown 10000:10000 /app && chown -R 10000:10000 /app/ruff /app/baseline-build
USER 10000:10000
WORKDIR /app
