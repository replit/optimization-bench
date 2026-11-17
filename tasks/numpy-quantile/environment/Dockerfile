FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS payload-guard
COPY . /payload
RUN python3 /payload/payload_check.py --fetch /payload && touch -d @0 /payload-ready

FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS compiled
COPY --from=payload-guard /payload-ready /payload-ready
COPY --from=payload-guard /payload/notices/ /app/notices/
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ gfortran libopenblas-dev pkg-config git curl && rm -rf /var/lib/apt/lists/*
COPY requirements-toolchain.txt /tmp/requirements-toolchain.txt
RUN python3 -m pip install --no-cache-dir -r /tmp/requirements-toolchain.txt

RUN curl -fL --retry 3 'https://files.pythonhosted.org/packages/95/b0/c7453d0b6e2073c3264468b106ee1563750cecc910965e67357e3698c83e/numpy-2.5.4.tar.gz' -o /tmp/source.tar.gz && echo '9a94cf751c9ad8ebaa835bcd3d40dacf8534ad086b88c38029b65123c7999d2a  /tmp/source.tar.gz' | sha256sum -c - && mkdir -p /app/numpy && tar -xzf /tmp/source.tar.gz -C /app/numpy --strip-components=1 && rm /tmp/source.tar.gz
WORKDIR /app/numpy
RUN export GIT_AUTHOR_NAME=optimbench GIT_AUTHOR_EMAIL=optimbench@invalid \
 GIT_COMMITTER_NAME=optimbench GIT_COMMITTER_EMAIL=optimbench@invalid \
 GIT_AUTHOR_DATE=2000-01-01T00:00:00+0000 GIT_COMMITTER_DATE=2000-01-01T00:00:00+0000 \
 && git -c init.defaultBranch=optimbench-baseline init -q \
 && git -c core.autocrlf=false -c core.filemode=true add -A \
 && git -c commit.gpgsign=false commit -qm 'numpy-quantile (pinned baseline)' \
 && test "$(git rev-parse HEAD^{tree})" = 1dd5136624a90576f4a5fcd687dc1a4ab6da471e \
 && test "$(git rev-parse HEAD)" = a0887b5f0eb0e202929b2f6d7d28c1615a90549e
COPY --from=payload-guard /payload/workload/ /app/workload/
COPY prepare.py /app/prepare.py
ENV PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=8 OMP_NUM_THREADS=8
RUN sh /app/workload/build.sh /app/baseline-build
WORKDIR /app

FROM compiled AS worker
COPY --from=payload-guard /payload-ready /payload-ready
COPY --from=payload-guard /payload/notices/ /app/notices/
RUN python3 /app/prepare.py /app/baseline-build && dpkg-query -W > /app/os-packages.lock
ENV PYTHONHASHSEED=0
