ARG TASK_IMAGE=python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
FROM ${TASK_IMAGE}
USER root
COPY tools/fuzz_runners/python.lock /opt/fuzz-runners/python.lock
RUN python -m venv --system-site-packages /opt/fuzz-python \
 && PIP_NO_INDEX=0 /opt/fuzz-python/bin/pip install --ignore-installed --require-hashes --only-binary=:all: -r /opt/fuzz-runners/python.lock \
 && /opt/fuzz-python/bin/python -c "import pathlib,site; pathlib.Path(site.getsitepackages()[0], 'optimbench_coverage.pth').write_text('import coverage; coverage.process_startup()\\n')"
# Do not replace the task interpreter. The venv uses the exact FROM interpreter.
# A different ABI (e.g. CPython 3.14) must resolve its own hash lock or emit
# unsupported; never silently downgrade the task to this reference interpreter.
ENV PATH=/opt/fuzz-python/bin:$PATH PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1
COPY tools/fuzz_runners /opt/fuzz-runners
WORKDIR /work
