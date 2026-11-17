# Build from repository root, AFTER the task image is built.
# Only this separate verifier stage receives tests/ and judge/.
ARG TASK_IMAGE
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS verifier-python-tools
FROM ${TASK_IMAGE}
USER root
COPY judge /opt/verifier/judge
COPY --from=verifier-python-tools /usr/local/lib/python3.12/site-packages/pip /opt/verifier/bootstrap/pip
RUN PYTHONPATH=/opt/verifier/bootstrap PIP_NO_INDEX=0 python3 -m pip install --no-deps --only-binary=:all: --require-hashes --target /opt/verifier/vendor -r /opt/verifier/judge/requirements-interface.lock
ENV PYTHONPATH=/opt/verifier/vendor:/opt/verifier
COPY tools/runner /opt/verifier/tools/runner
COPY tasks/goldmark-gfm-html /opt/verifier/tasks/goldmark-gfm-html
RUN PYTHONPATH=/opt/verifier python3 -m judge.heldout /opt/verifier/tasks/goldmark-gfm-html/tests /opt/verifier/heldout-projects
ENTRYPOINT ["/opt/verifier/tasks/goldmark-gfm-html/tests/test.sh"]
