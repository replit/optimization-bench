FROM python@sha256:7bec7ddcddeff7975d6ba9b4be7dd6f6b2f55e7491539145e2978f7f97ce9144
RUN python -c "import site,pathlib; pathlib.Path(site.getsitepackages()[0],'optimbench_parent_dependency.py').write_text(\"VALUE = 'original-parent'\\n\")"
ENV PIP_NO_INDEX=1
