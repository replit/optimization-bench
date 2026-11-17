FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS suite-inputs
RUN apt-get update && apt-get install -y --no-install-recommends p7zip-full ca-certificates && rm -rf /var/lib/apt/lists/*
COPY payload_check.py payload-public.json /payload/
COPY workload/inputs/files /payload/workload/inputs/files
COPY workload/driver/derive.py workload/driver/derive-public.json /derive/
RUN python3 /payload/payload_check.py --fetch /payload && PYTHONHASHSEED=0 python3 /derive/derive.py /derive/derive-public.json /payload/workload/inputs/files --jobs 8 --prune

FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev make git tcl-dev ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=suite-inputs /payload/workload/inputs/files /app/workload/inputs/files
COPY prepare.py source.lock.json /tmp/prepare/
RUN python3 /tmp/prepare/prepare.py && rm -rf /tmp/prepare
RUN test "$(git -C /app/sqlite rev-parse HEAD)" = 4e225016bd10704fbdfe7d2decdd4c04a285d91e && test "$(git -C /app/sqlite rev-parse HEAD^{tree})" = 0ea58193086cadd4ae4fb73e1d712b9419f08b69
COPY workload/runner.py workload/task.py workload/build.sh workload/check_public.py workload/corpus.json /app/workload/
COPY workload/driver /app/workload/driver
COPY workload/inputs/manifest.json /app/workload/inputs/manifest.json
COPY notices /app/notices
ENV PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=8
RUN sh /app/workload/build.sh /app/baseline-build && python3 /app/workload/check_public.py /app/baseline-build && dpkg-query -W > /app/os-packages.lock
WORKDIR /app
