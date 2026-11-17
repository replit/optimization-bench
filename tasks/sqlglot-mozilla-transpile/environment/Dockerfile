FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS payload-guard
RUN --mount=type=bind,target=/payload,readonly \
 python3 /payload/payload_check.py /payload && touch -d @0 /payload-ready

FROM python@sha256:7bec7ddcddeff7975d6ba9b4be7dd6f6b2f55e7491539145e2978f7f97ce9144
COPY --from=payload-guard /payload-ready /payload-ready
COPY notices/ /app/notices/
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY requirements-tests.lock /tmp/requirements-tests.lock
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements-tests.lock && rm /tmp/requirements-tests.lock
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 PIP_NO_CACHE_DIR=1 PIP_NO_INDEX=1
COPY prepare.py source.lock.json /tmp/prepare/
RUN python /tmp/prepare/prepare.py && rm -rf /tmp/prepare
COPY source-overlay/ /app/sqlglot/
WORKDIR /app/sqlglot
RUN export GIT_AUTHOR_NAME=optimbench GIT_AUTHOR_EMAIL=optimbench@invalid \
 GIT_COMMITTER_NAME=optimbench GIT_COMMITTER_EMAIL=optimbench@invalid \
 GIT_AUTHOR_DATE=2000-01-01T00:00:00+0000 GIT_COMMITTER_DATE=2000-01-01T00:00:00+0000 \
 && git -c init.defaultBranch=optimbench-baseline init -q \
 && git -c core.autocrlf=false -c core.filemode=true add -A \
 && git -c core.autocrlf=false -c core.filemode=true add -f sqlglot/_version.py sqlglot.egg-info \
 && git -c commit.gpgsign=false commit -qm "SQLGlot 5d16fa8 (pinned baseline)" \
 && test "$(git rev-parse HEAD^{tree})" = 003865d7f0193ea93e8930c61741842e66f56e72 \
 && test "$(git rev-parse HEAD)" = 62a294d494639815defd6b06a530d608e27d84a2
COPY workload/build.sh workload/runner.py workload/task.py /app/workload/
COPY workload/driver/ /app/workload/driver/
COPY workload/inputs/manifest.json /app/workload/inputs/manifest.json
# Public input bodies, including the fetched/derived TPC inputs, use a transient
# read-only mount and are never image layers; agents receive the same root read-only.
RUN --mount=type=bind,source=workload/inputs/files,target=/app/workload/inputs/files,readonly \
 chmod 755 /app/workload/build.sh \
 && sh /app/workload/build.sh /app/baseline-build \
 && /app/baseline-build/run > /tmp/baseline.txt \
 && python -c "import json,sys; m=json.load(open('/app/workload/inputs/manifest.json')); want={d['path']:(d['expect_len'],d['expect_sha']) for s in m['splits'].values() for b in s for d in b['docs']}; got={r[0]:(int(r[1]),r[2]) for r in (l.split() for l in open('/tmp/baseline.txt'))}; sys.exit(got!=want)" \
 && rm /tmp/baseline.txt \
 && dpkg-query -W > /app/os-packages.lock && python -m pip freeze --all > /app/python-packages.lock
WORKDIR /app
