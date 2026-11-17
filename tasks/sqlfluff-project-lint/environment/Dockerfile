FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS payload-guard
COPY . /payload
RUN python3 /payload/payload_check.py --fetch /payload && touch -d @0 /payload-ready

FROM python@sha256:7bec7ddcddeff7975d6ba9b4be7dd6f6b2f55e7491539145e2978f7f97ce9144
COPY --from=payload-guard /payload-ready /payload-ready
COPY --from=payload-guard /payload/notices/ /app/notices/
RUN apt-get update && apt-get install -y --no-install-recommends build-essential git && rm -rf /var/lib/apt/lists/*
COPY requirements.lock /tmp/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock && rm /tmp/requirements.lock
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 PIP_NO_CACHE_DIR=1 PIP_NO_INDEX=1
COPY prepare.py source.lock.json public-workloads.lock.json /tmp/prepare/
RUN python /tmp/prepare/prepare.py && rm -rf /tmp/prepare
WORKDIR /app/sqlfluff
RUN export GIT_AUTHOR_NAME=optimbench GIT_AUTHOR_EMAIL=optimbench@invalid \
 GIT_COMMITTER_NAME=optimbench GIT_COMMITTER_EMAIL=optimbench@invalid \
 GIT_AUTHOR_DATE=2000-01-01T00:00:00+0000 GIT_COMMITTER_DATE=2000-01-01T00:00:00+0000 \
 && git -c init.defaultBranch=optimbench-baseline init -q \
 && git -c core.autocrlf=false -c core.filemode=true add -A \
 && git -c commit.gpgsign=false commit -qm "SQLFluff 0348222 (pinned baseline)" \
 && test "$(git rev-parse HEAD^{tree})" = bfbce0ef42b9cc50e6ed4375141502a07698225a \
 && test "$(git rev-parse HEAD)" = 723150396a9445f75bdfef3d6e9f971df2a93711
COPY --from=payload-guard /payload/workload/ /app/workload/
RUN chmod 755 /app/workload/build.sh /app/workload/guards/capture_autofix.py \
 && sh /app/workload/build.sh /app/baseline-build \
 && /app/baseline-build/run > /tmp/baseline.txt \
 && python -c "import json,sys; m=json.load(open('/app/workload/inputs/manifest.json')); want={d['path']:(d['expect_len'],d['expect_sha']) for s in m['splits'].values() for b in s for d in b['docs']}; got={r[0]:(int(r[1]),r[2]) for r in (l.split() for l in open('/tmp/baseline.txt'))}; sys.exit(got!=want)" \
 && rm /tmp/baseline.txt \
 && dpkg-query -W > /app/os-packages.lock && python -m pip freeze --all > /app/python-packages.lock
WORKDIR /app
