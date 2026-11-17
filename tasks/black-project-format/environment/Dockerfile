FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS payload-guard
COPY . /payload
RUN python3 /payload/payload_check.py --fetch /payload && touch -d @0 /payload-ready

# Worker environment for optimbench/black-project-format: the pinned Black source (87928e6) committed
# to a local git repository, Python 3.14.6 with the hash-locked build, test and pre-commit dependencies,
# the protected standalone driver, public development cases, the original Django, mypy and Tornado
# trees, and a prebuilt native baseline. Network is used only while building (pinned archives + hashes).
FROM python@sha256:7bec7ddcddeff7975d6ba9b4be7dd6f6b2f55e7491539145e2978f7f97ce9144
COPY --from=payload-guard /payload-ready /payload-ready
COPY --from=payload-guard /payload/notices/ /app/notices/

RUN apt-get update && apt-get install -y --no-install-recommends build-essential git && rm -rf /var/lib/apt/lists/*
COPY requirements.lock /tmp/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock && rm /tmp/requirements.lock
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 PIP_NO_CACHE_DIR=1 PIP_NO_INDEX=1

COPY prepare.py source.lock.json public-workloads.lock.json /tmp/prepare/
RUN python /tmp/prepare/prepare.py source /app/black \
 && python /tmp/prepare/prepare.py projects /app/projects \
 && rm -rf /tmp/prepare
WORKDIR /app/black
RUN export GIT_AUTHOR_NAME=optimbench GIT_AUTHOR_EMAIL=optimbench@invalid \
 GIT_COMMITTER_NAME=optimbench GIT_COMMITTER_EMAIL=optimbench@invalid \
 GIT_AUTHOR_DATE=2000-01-01T00:00:00+0000 GIT_COMMITTER_DATE=2000-01-01T00:00:00+0000 \
 && git -c init.defaultBranch=optimbench-baseline init -q \
 && git -c core.autocrlf=false -c core.filemode=true add -A \
 && git -c commit.gpgsign=false commit -qm "Black 87928e6 (pinned baseline)" \
 && test "$(git rev-parse HEAD^{tree})" = e75a5a079beaebcd2831d7d45b722dd7f338f34f \
 && test "$(git rev-parse HEAD)" = 674dd88e5d714d97ea22bc77c34bb64783513a9f

RUN apt-get update && apt-get install -y --no-install-recommends ccache && rm -rf /var/lib/apt/lists/*
COPY --from=payload-guard /payload/workload/ /app/workload/
RUN chmod 755 /app/workload/build.sh \
 && sh /app/workload/build.sh /app/baseline-build \
 && /app/baseline-build/run > /tmp/baseline.txt && python3 -c "import json,sys; m=json.load(open('/app/workload/inputs/manifest.json')); want={d['path']:(d['expect_len'],d['expect_sha']) for s in m['splits'].values() for b in s for d in b['docs']}; got={r[0]:(int(r[1]),r[2]) for r in (l.split() for l in open('/tmp/baseline.txt'))}; sys.exit(got!=want)" \
 && rm /tmp/baseline.txt \
 && dpkg-query -W > /app/os-packages.lock && python -m pip freeze --all > /app/python-packages.lock
WORKDIR /app
ENV BLACK_NUM_WORKERS=8
