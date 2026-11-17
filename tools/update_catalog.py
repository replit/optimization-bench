#!/usr/bin/env python3
"""Generate the task table and public-execution selections from authenticated payloads."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from heldout_policy import output_coincidences
import subprocess
import shutil
import tomllib
from stage_payload import ROOT, missing, git_history_roots
from render_instruction import TEMPLATE as PROMPT_TEMPLATE, defaults as prompt_defaults, templatize

START = "<!-- task-table:start -->"
# Reader-facing wording for the stored payload status (the stored values are unchanged).
TABLE_STATE = {"ready": "in repository", "heldout-missing": "fetched at scoring",
               "public-payload-missing": "public inputs fetched at setup"}
END = "<!-- task-table:end -->"
RULES_START = "<!-- agent-rules:start -->"
RULES_END = "<!-- agent-rules:end -->"
CPU_POLICY = ("You may use the task's eight CPUs however you see fit (threads, SIMD, parallel algorithms) "
              "as long as outputs, interfaces and all other rules hold; results report both wall-time "
              "speedup and total CPU time.")
OS_JOIN_POLICY = (
    "Any threads you create must be fully joined at the OS level before the public call returns; "
    "in Rust, join every ScopedJoinHandle/JoinHandle explicitly (std::thread::scope's implicit join "
    "does not guarantee the OS thread has exited); in C/C++, pthread_join/std::thread::join; "
    "detached threads, persistent pools, or threads still running after return are rejected.")
PROCESS_GLOBAL_STATE_POLICY = (
    "Do not change process-global runtime or OS state that the original library leaves alone "
    "(e.g., GC/GOGC/GOMAXPROCS/memory limits, Python gc or interpreter settings, malloc tunables, "
    "signal handlers, environment, locale, cwd, umask, rlimits, CPU affinity); such changes are "
    "rejected as contract regressions.")
CANONICAL_RUNNER = Path(__file__).with_name("runner") / "runner.py"
LFS_POINTER = b"version https://git-lfs.github.com/spec/v1\n"
PROMPT_REPOSITORY = Path(__file__).resolve().parents[1]


def task_paths(tasks=None):
    """Explicit task scope; never expand a requested subset to the repository."""
    available = sorted(path for path in (ROOT / "tasks").iterdir() if (path / "task.json").is_file())
    if tasks is None:
        return available
    result = []
    for value in tasks:
        path = Path(value)
        path = path if path.is_absolute() else ROOT / "tasks" / path
        path = path.resolve()
        if path not in [item.resolve() for item in available]:
            raise ValueError("Unknown task in requested catalog scope: " + path.name)
        if path in result:
            raise ValueError("Duplicate task in catalog scope")
        result.append(path)
    return result


def unavailable_inputs(task):
    """Only missing/unhydrated LFS is unavailable; real corruption is fatal."""
    from stage_payload import contained, authentic
    from payload_check import repo_lfs_spec
    task = Path(task)
    pending = []
    def pointer(path):
        if not path.is_file():
            return False
        with path.open("rb") as stream:
            return stream.read(128).startswith(LFS_POINTER)
    for name in ("task.json", "payload.json", "provenance.json", "qualification-status.json",
                 "tests/heldout.lock.json", "tests/heldout/manifest.json",
                 "tests/heldout/baseline-outputs.json"):
        path = task / name
        if pointer(path):
            pending.append({"path": name, "scope": "heldout" if name.startswith("tests/") else "metadata",
                            "reason": "unhydrated Git LFS metadata"})
        elif path.is_file():
            json.loads(path.read_text())  # Malformed materialized JSON is not unavailability.
    if pointer(task / "payload.json"):
        return pending
    payload = json.loads((task / "payload.json").read_text())
    # Availability never waives path/reference policy. Validate declarations and
    # forbidden retained reference bodies before skipping an unfetched packet.
    if not pointer(task / "tests/heldout.lock.json"):
        git_history_roots(task, payload)
    for entry in payload["files"]:
        if entry.get("retention") == "fetch-only":
            continue  # Private reference bodies must not be staged into the checkout.
        target = contained(task, entry["destination"])
        if pointer(target):
            pending.append({"path": entry["destination"], "scope": entry["scope"],
                            "reason": "unhydrated Git LFS body"})
        elif target.is_file() and entry.get("sha256") and not authentic(target, entry):
            raise ValueError("Materialized payload size/SHA256 corruption: " + task.name + "/" + entry["destination"])
        elif (not target.exists() and entry.get("sha256") and entry.get("kind") != "git-history"
              and not entry.get("fetch") and not entry.get("url") and not entry.get("transport")
              and entry.get("kind") != "repo-lfs"):
            pending.append({"path": entry["destination"], "scope": entry["scope"],
                            "reason": "declared retained body unavailable; recover its scoped LFS/operator transport"})
        if entry.get("kind") == "repo-lfs":
            source = contained(task, repo_lfs_spec(entry))
            if not source.is_file() or pointer(source):
                pending.append({"path": entry["source"], "scope": "public",
                                "reason": "public repo-lfs recovery body unavailable"})
            elif not authentic(source, entry):
                raise ValueError("Materialized public repo-lfs size/SHA256 corruption: " + task.name + "/" + entry["source"])
    return pending


def reported_memory_policy(text):
    """Correct legacy RSS-budget prose; never alter semantic API memory oracles."""
    for previous in (
        "- Peak memory may not exceed 1.25x the baseline's.",
        "- Peak memory of an invocation may not exceed 1.25x the baseline's.",
    ):
        text = text.replace(previous, "- Peak memory is measured and reported, but is not gated.")
    return text.replace("peak RSS <= 1.25x baseline.",
                        "peak RSS is measured and reported, not gated.")


def neutral_prompt_tasks():
    """Legacy packages retain their original prompt format and version."""
    selected = json.loads((PROMPT_REPOSITORY / "datasets/v1.0.json").read_text())
    versions = selected.get("task_versions", {})
    return {task for task in selected["tasks"]
            if versions.get(task, selected.get("task_version", "1.0.0")) == "1.0.0"}


def neutral_agent_instructions(task):
    """Generate shared prose from the reviewed template, retaining task facts."""
    document = (PROMPT_REPOSITORY / "docs/agent-prompt-template.md").read_text()
    template = document.split("````markdown\n", 1)[1].split("\n````", 1)[0]
    text = (task / "instruction.md").read_text()
    for heading in ("Goal", "Setup", "How it is evaluated", "You may"):
        pattern = rf"(?m)^## {heading}\n.*?(?=^## |\Z)"
        block = re.search(pattern, template, re.S).group()
        if len(re.findall(pattern, text, re.S)) != 1:
            raise ValueError(f"{task.name}: expected one {heading} section")
        text = re.sub(pattern, lambda _: block, text, flags=re.S)
    lines = template.split("## Rules\n", 1)[1].splitlines()
    rules = lines[:next(index for index, line in enumerate(lines) if not line.startswith("- "))]
    pattern = rf"(?m)^## Rules\n(?:- [^\n]*\n){{{len(rules)}}}"
    if len(re.findall(pattern, text)) != 1:
        raise ValueError(f"{task.name}: expected {len(rules)} shared Rules bullets")
    return re.sub(pattern, lambda _: "## Rules\n" + "\n".join(rules) + "\n", text)


def prompt_editable_files(task, text):
    """Expand a closed-set prompt rule; never infer it from allowed_files."""
    section = text.split("## What you may edit\n", 1)[1].split("\n## ", 1)[0]
    lock = json.loads((task / "environment/pristine-files.lock.json").read_text())
    pristine = set(lock.get("files_sha256", lock))
    if "files are editable: " in section:
        # Literal inventories need no glob expansion. Git's protected-only
        # pristine lock omits editable xdiff files, so do not pretend it proves
        # that a directory-wide rule is complete.
        names = re.findall(r"`([^`]+)`", section.split("files are editable: ", 1)[1].split(". ", 1)[0])
        if len(names) != len(set(names)):
            raise ValueError(f"{task.name}: duplicate editable filename")
        return set(names)
    match = re.search(r"every existing (.*?) file under (.*?) is editable except files (.*?)\. ", section)
    if not match:
        raise ValueError(f"{task.name}: unrecognized closed-set edit rule")
    extensions = re.findall(r"`([^`]+)`", match[1])
    roots = re.findall(r"`([^`]+)`", match[2])
    if match[3] == "whose name contains `test`":
        excluded = lambda p: "test" in p.name
    elif match[3] == "ending in `*_test.go`":
        excluded = lambda p: p.name.endswith("_test.go")
    else:
        raise ValueError(f"{task.name}: unrecognized edit-rule exclusion")
    return {name for name in pristine
            if Path(name).suffix in extensions
            and any(name.startswith(root + "/") for root in roots)
            and not excluded(Path(name))}


def prose_word_limit(task):
    """800 words for one workload; 40 more per extra declared public unit, at most 1,400."""
    path = task / "tests/judge.json"
    settings = json.loads(path.read_text()) if path.exists() else {}
    units = len(settings.get("performance_units", {}).get("public") or {}) or 1
    return min(800 + 40 * (units - 1), 1400)


def validate_neutral_prompt(task):
    """Validate prompt-only invariants without certifying payloads or scoring."""
    text = (task / "instruction.md").read_text()
    if text != neutral_agent_instructions(task):
        raise ValueError(f"{task.name}: stale neutral shared sections")
    if "\n## Editable files" in text:
        raise ValueError(f"{task.name}: editable appendices are not permitted")
    main = text
    banned = (r"parallel|thread|SIMD|profil|BLACK_NUM_WORKERS|CharLS|draft|Harbor 1.4|"
              r"GCP|v1.0|v1.1|median speedup|gated at 1/2/4/8")
    if re.search(banned, main):
        raise ValueError(f"{task.name}: prohibited prompt wording")
    words = len(re.sub(r"```.*?```", "", main, flags=re.S).split())
    limit = prose_word_limit(task)
    if not 350 <= words <= limit:
        raise ValueError(f"{task.name}: prose word count {words} outside 350–{limit}")
    policy = json.loads((task / "release.json").read_text())["source_policy"]
    for extension in policy["allowed_extensions"]:
        if f"`{extension}`" not in main:
            raise ValueError(f"{task.name}: missing editable extension {extension}")
    if "allowed_files" in policy:
        if prompt_editable_files(task, text) != set(policy["allowed_files"]):
            raise ValueError(f"{task.name}: editable rule differs from source policy")
    config = tomllib.loads((task / "task.toml").read_text())
    if (config["environment"]["cpus"], config["environment"]["memory_mb"],
            config["agent"]["timeout_sec"]) != (8, 16384, 7200):
        raise ValueError(f"{task.name}: setup differs from neutral resource facts")


def agent_instructions(task):
    if task.name in neutral_prompt_tasks():
        return neutral_agent_instructions(task)
    text = reported_memory_policy((task / "instruction.md").read_text())
    # Migrate legacy per-task prohibitions through the canonical generator.
    for old, new in (
        ("Forbidden: threads; caching", "Forbidden: caching"),
        ("Forbidden: threads or other concurrency in the decode path; caching", "Forbidden: caching"),
        ("persistent result cache, new threads or global state changes.",
         "persistent result cache or global state changes unrelated to allowed parallelism."),
        ("one thread; native candidate threads may use the assigned core budget. The",
         "one thread in the frozen driver configuration. The"),
        ("- Per invocation, the exit status, stdout, every declared output file (every\n"
         "  file left in the tree for `-w`) and stderr stay byte-identical to the\n"
         "  pinned baseline. stderr is compared after hexadecimal addresses are masked.",
         "- Per invocation, exit status, stdout and every declared output file (every\n"
         "  file left in the tree for `-w`) stay byte-identical to the pinned baseline.\n"
         "  CLI stderr preserves error headlines/classes, not stack frames or addresses."),
        ("- Per operation, the exit status, stdout, stderr (compared line by line in\n"
         "  sorted order, since parallel workers interleave lines) and every file and\n"
         "  symlink left in the project tree stay byte-identical to the pinned baseline.",
         "- Per operation, exit status, stdout and every file and symlink left in the\n"
         "  project tree stay byte-identical to the pinned baseline. CLI stderr retains\n"
         "  error headlines/classes; implementation stack frames and addresses are ignored."),
        ("exit status, stdout and stderr. Normal `-C` selects the protected checkout.",
         "exit status and stdout. CLI stderr retains error headlines/classes, not\n"
         "implementation stack frames or addresses. Normal `-C` selects the protected checkout."),
        ("1/2/4-core regression checks belong to the protected verifier.",
         "Correctness is gated at 1/2/4/8 CPUs. Low-core timing is report-only,\n"
         "optional and off by default in official sessions; it is not a regression gate."),
        ("held-out results, and pass resource, regression and timing controls before scores",
         "held-out results, and pass correctness, isolation and timing-quality controls before scores"),
    ):
        text = text.replace(old, new)
    if RULES_START in text:
        before, rest = text.split(RULES_START, 1)
        _, after = rest.split(RULES_END, 1)
        text = before.rstrip() + "\n\n" + after.lstrip()
    seconds = int(tomllib.loads((task / "task.toml").read_text())["agent"]["timeout_sec"])
    rules = (
        f"{RULES_START}\n## Working rules\n\n"
        f"You have up to {seconds // 3600} hours ({seconds:,} seconds), as configured by the agent timeout. "
        "The editable source present when time runs out is what gets judged. Keep profiling, optimizing, "
        "and verifying correctness until the budget is nearly exhausted rather than stopping after a first improvement.\n\n"
        "New tests and new fixture files may be added, including new cases in an existing test file. "
        "New tests must pass (skip/xfail markers on new tests are allowed). Do not modify, remove, or rename "
        "existing tests, fixtures, or snapshots; every original test identity and outcome must stay identical. "
        "Adding tests does not authorize changing original cases or their pass/skip/xfail/deselect status.\n\n"
        "Preserve the caller's process-wide runtime configuration. Do not change global GC or allocator "
        "settings, memory limits, process counts, environment variables, interpreter/JIT/VM flags, "
        "signal handlers, or similar process-global state as a side effect of importing or calling library code. "
        "Preserve the public interface: adding public items is allowed; removing or incompatibly changing them is not.\n"
        f"\n{CPU_POLICY}\n\n"
        "Library-level parallelism is allowed; the driver's frozen concurrency settings are workload "
        "parameters and must not change (including --threads arguments, BLACK_NUM_WORKERS and -p 8). "
        "No worker processes or threads may outlive the measured operation. Caching across calls, "
        "input recognition and all other forbidden shortcuts remain forbidden.\n"
        f"\n{OS_JOIN_POLICY}\n"
        f"\n{PROCESS_GLOBAL_STATE_POLICY}\n"
        "\nCLI diagnostics use cli-stderr-contract-v1: preserve error headlines/classes and exit status; "
        "implementation stack frames, goroutine identities and memory addresses are not contract data. "
        "Stdout and file side effects remain exact. Raw stderr is retained in protected evidence.\n"
        "\nCorrectness is gated at 1/2/4/8 CPUs. Official performance timing uses 8 CPUs. "
        "Low-core timing is an optional report-only diagnostic, not a regression gate, and is "
        "not executed by default or used to reject an official score. Memory is reported, not ratio-gated.\n"
        f"{RULES_END}\n\n")
    anchor = "## Exact edit policy"
    if anchor in text:
        return text.replace(anchor, rules + anchor, 1)
    return text.rstrip() + "\n\n" + rules.rstrip() + "\n"


def normalize_distribution(tasks=None):
    result = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True)
    tracked = set(result.stdout.decode().split("\0")) if result.returncode == 0 else set()
    for task in task_paths(tasks):
        if unavailable_inputs(task):
            continue
        definition_path = task / "task.json"
        if definition_path.is_file():
            definition = json.loads(definition_path.read_text())
            contract = definition.get("contract")
            if isinstance(contract, str):
                updated = contract.replace("no threads, ", "").replace(
                    "No new global state tuning,caches,threads,dependency",
                    "No new global state tuning unrelated to allowed parallelism,caches,dependency")
                if updated != contract:
                    definition["contract"] = updated
                    definition_path.write_text(json.dumps(definition, indent=2) + "\n")
            limits = definition.get("limits")
            if isinstance(limits, str) and "the timed CPU is pinned." in limits:
                definition["limits"] = limits.replace(
                    "the timed CPU is pinned.",
                    "the shared verifier runs timed work within its assigned eight-CPU set.")
                definition_path.write_text(json.dumps(definition, indent=2) + "\n")
        path = task / "payload.json"
        if not path.is_file():
            continue
        payload = json.loads(path.read_text())
        for entry in payload["files"]:
            if entry["scope"] != "public":
                continue
            target = task / entry["destination"]
            entry["distribution"] = ("upstream" if (
                entry.get("fetch") or entry.get("kind") == "git-history" or
                entry.get("url", "").startswith("https://") or
                entry.get("transport", {}).get("url", "").startswith("https://")) else
                "git" if target.is_file() and target.relative_to(ROOT).as_posix() in tracked else "operator")
        path.write_text(json.dumps(payload, indent=2) + "\n")
        subset = {"schema_version": 1, "task": task.name, "files": [
            {**entry, "destination": entry["destination"].removeprefix("environment/")}
            for entry in payload["files"] if entry["scope"] == "public"]}
        (task / "environment/payload-public.json").write_text(json.dumps(subset, indent=2) + "\n")
        notices = task / "environment/notices"
        notices.mkdir(parents=True, exist_ok=True)
        for source_root, prefix in ((task / "notices", Path(".")), (task / "public", Path("public"))):
            if not source_root.is_dir():
                continue
            for source in source_root.rglob("*"):
                if not source.is_file():
                    continue
                relative = source.relative_to(source_root)
                if source_root.name == "public" and "notices" not in relative.parts:
                    continue
                target = notices / prefix / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if source.resolve() != target.resolve():
                    shutil.copyfile(source, target)
        (notices / "DISTRIBUTION.md").write_text(
            "# Verbatim task notices\n\nOriginal task-supplied notices are retained byte-for-byte here. "
            "Source/dependency licenses also remain with their pinned source trees. "
            "No new license conclusions are inferred by consolidation.\n")


def distribution(task):
    payload = json.loads((task / "payload.json").read_text())
    entries = [entry for entry in payload["files"] if entry["scope"] == "public"]
    totals = {kind: sum((entry["object_inventory"]["bytes"] if entry.get("kind") == "git-history" else entry["bytes"] or 0) for entry in entries
                       if ("repo-lfs" if entry.get("kind") == "repo-lfs" else
                           entry.get("distribution", "operator")) == kind)
              for kind in ("git", "upstream", "operator", "repo-lfs")}
    urls = set()
    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "url" and isinstance(child, str):
                    urls.add(child)
                elif key == "alternate_urls":
                    urls.update(child)
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    for entry in entries:
        visit(entry.get("fetch", {}))
        for url in (entry.get("url", ""), entry.get("transport", {}).get("url", "")):
            if url.startswith("https://"):
                urls.add(url)
        if entry.get("kind") == "git-history":
            urls.add(entry["repository"])
    return {**totals, "upstream_urls": sorted(urls)}


def reviewed_image_policy(task):
    """The reviewed image policy for a Task (see datasets/public-release-ledger.json)."""
    ledger = ROOT / "datasets/public-release-ledger.json"
    if not ledger.is_file():
        return None
    return json.loads(ledger.read_text()).get("tasks", {}).get(task.name, {}).get("image_policy")


def task_readmes(task, status):
    definition = json.loads((task / "task.json").read_text())
    edition_note = definition.get("input_edition_note")
    if "input_edition_note" in definition and (not isinstance(edition_note, str) or not edition_note.strip()):
        raise ValueError("input_edition_note must be a nonempty authored statement")
    title = definition.get("title", task.name)
    job = definition.get("job", f"Optimization task `{task.name}`.")
    data = distribution(task)
    dockerfile = task / "environment/Dockerfile"
    declared_packet = (task / "environment/public-input-packet.json").is_file()
    external_inputs = declared_packet or (dockerfile.is_file() and "target=/app/workload/inputs" in dockerfile.read_text())
    contract = definition.get("contract", "The original behavioral and edit contract is in instruction.md and task.json.")
    lock_path = task / "tests/heldout.lock.json"
    lock = json.loads(lock_path.read_text()) if lock_path.is_file() else {}
    expected_text = ("Expected outputs are stored with Git LFS."
                     if any(name.startswith("heldout/expected/") for name in lock.get("files", {})) else
                     "Expected-result commitments remain authenticated by the held-out manifest.")
    heldout_text = (
        "Held-out inputs are fetch-only; verification acquires and authenticates them "
        "outside the repository. " + expected_text
        if lock.get("references") else
        "Held-out assets are available via Git LFS and are authenticated at scoring."
        if lock.get("status") == "available" else
        "Held-out assets are unavailable; scored verification fails closed.")
    # An authored statement overrides the default when a task's held-out
    # distribution is mixed (some bodies in LFS, some fetched or regenerated).
    override = definition.get("heldout_readme_text")
    if "heldout_readme_text" in definition and (not isinstance(override, str) or not override.strip()):
        raise ValueError("heldout_readme_text must be a nonempty authored statement")
    if override:
        heldout_text = override
    if task.name == "sqlfmt-cli":
        job = job.replace("for private evaluation", "for held-out evaluation")
        heldout_text = "Held-out assets are authenticated at scoring."
    lines = [f"# {title}", "", job, "",
        "## Contract", "", str(contract), "",
        "The workload, editable scope, prohibitions, source pins and expected results are",
        "defined in [instruction.md](instruction.md), [task.json](task.json) and",
        "[release.json](release.json)." + (" " + edition_note if edition_note else ""), "",
        "## Input distribution", "", f"- Public cases: {definition.get('example_count', '?')}.",
        f"- Held-out inputs: {TABLE_STATE.get(status, status)}.",
        f"- Input bodies stored in Git: {data['git']:,} bytes.",
        (f"- Input bodies acquired from authenticated upstreams before image build: {data['upstream']:,} bytes."
         if external_inputs else
         f"- Input bodies reconstructed from authenticated upstreams at image build: {data['upstream']:,} bytes.")]
    if data["operator"]:
        lines.append(f"- Input bodies supplied from a staged public packet: {data['operator']:,} bytes.")
    lines += ["",
        "Exact URLs, pinned hashes and sizes are in [payload.json](payload.json). All",
        "downloads happen before compilation and execution, never during a measured workload.", "",
        "## Build and run", "", "From the repository root:", "", "```sh",
        f"python3 tools/check_tasks.py --task {task.name}"]
    if data["operator"]:
        lines.append(f"python3 tools/stage_payload.py --task {task.name} --from /path/to/original-public-packet")
    if data["repo-lfs"]:
        lines.append(f"git lfs pull --include='tasks/{task.name}/public/files/**' --exclude=''")
    if data["repo-lfs"] or external_inputs:
        lines.append(f"python3 tools/stage_payload.py --task {task.name} --fetch")
    if declared_packet:
        lines += [
            f"python3 -m judge.public_input_preflight --task {task.name} --fetch",
            f"python3 -m tools.public_runtime_packet --task {task.name} \\",
            f"  --cache /absolute/trusted-cache --out /absolute/new-{task.name}-agent-packet", "```", "",
            "The packet declaration defines the exact read-only runtime input roots.",
            "Acquisition archives are not implicitly visible to the agent. An agent run",
            "mounts the packet read-only; the source is then exported and scored separately. See",
            "[public runtime packets](../../docs/public-runtime-packets.md).", ""]
    elif external_inputs:
        lines += [
            f"""recipe=$(python3 -c 'from judge.image_recipe import descriptor; print(descriptor("tasks/{task.name}")["recipe_sha256"])')""",
            "DOCKER_BUILDKIT=1 docker build --provenance=false \\",
            '  --label "org.optimbench.recipe-sha256=$recipe" \\',
            f"  -t optimbench:{task.name} tasks/{task.name}/environment",
            f'packet="$PWD/tasks/{task.name}/environment/workload/inputs/files"',
            "docker run --rm --network none \\",
            '  --mount "type=bind,source=$packet,target=/app/workload/inputs/files,readonly" \\',
            f"  optimbench:{task.name} /app/baseline-build/run", "```", "",
            "These commands mount the authenticated public input files read-only at",
            "`/app/workload/inputs/files`; the image does not contain them. An agent run",
            "needs the same read-only mount.", ""]
    else:
        lines += [f"docker build -t optimbench:{task.name} tasks/{task.name}/environment",
            f"docker run --rm --network none optimbench:{task.name} /app/baseline-build/run", "```", ""]
    if declared_packet or external_inputs:
        lines += [
            "On a hosted sandbox that cannot mount these inputs, run",
            f"`python3 tools/fetch_public_inputs.py --task {task.name}` from a temporary checkout",
            "inside the container before the agent starts, then delete the checkout; see",
            "[Running an agent](../../README.md#running-an-agent).", ""]
    if reviewed_image_policy(task) == "ecr-after-signoff":
        lines += [
            "A prebuilt image is published to Public ECR and pinned by digest in",
            "`task.toml`; you can also build it locally from `environment/`.", ""]
    else:
        lines += ["No prebuilt image is published for this task; build it locally from `environment/`.", ""]
    lines += [
        "To run an agent, start it in this image with a 2-hour (7,200 s) limit. The source",
        "left in the editable paths at the time limit is evaluated and then scored with",
        "`judge.score`; see [agent-runner.md](../../docs/agent-runner.md).", ""]
    lines += ["## Verification", "",
        "All execution uses the common [runner](../../docs/runner.md). The",
        "[verifier](../../docs/judge.md) configuration and the original upstream test suite",
        "are under `tests/`. " + heldout_text, ""]
    if data["repo-lfs"]:
        lines += [f"Public inputs stored with Git LFS total {data['repo-lfs']:,} bytes. They are kept in",
                  "`public/files/`, outside the agent context, and staging authenticates them",
                  "before builds. Held-out LFS bodies are not needed for public builds.", ""]
    judge_path = task / "tests/judge.json"
    judge_settings = json.loads(judge_path.read_text()) if judge_path.is_file() else {}
    notes = definition.get("readme_notes", judge_settings.get("qualification_notes"))
    if task.name == "sqlfmt-cli":
        lines += [
            "Local reproduction, source-only exports and data terms are documented in",
            "[tests/README.md](tests/README.md). All public SQL bodies are fetch-only.", ""]
    elif notes:
        lines += [str(notes), ""]
    if (task / "notices").is_dir():
        lines += ["Retain all original attribution in [notices/](notices/).", ""]
    elif (task / "environment/notices").is_dir():
        lines += ["Input licences, attributions and changes made are in",
                  "[environment/notices/](environment/notices/); retain them when redistributing.", ""]
    if (task / "tests/notices").is_dir():
        lines += ["Held-out input licences and attributions are in [tests/notices/](tests/notices/).", ""]
    input_context = (
        "This directory retains the original source, compiler and guard contract. "
        "`public-input-packet.json` declares exact read-only runtime data roots and "
        "any original pinned public derivation. Use the shared public runtime packet "
        "helper before agent startup; acquisition archives are not automatically visible. "
        "The generated agent command is agent-only. Genuine correctness "
        "and performance judging runs separately from policy-admitted source exports. "
        "Audit all image layers and metadata before publication; local acquisition "
        "does not grant redistribution.\n\n"
        if declared_packet else
        (
        "This directory stages public inputs for the original build guards; it never "
        "contains verifier or held-out data. The Dockerfile uses transient read-only "
        "public input mounts. Stage and authenticate the public packet before building. "
        "Do not assume that the image contains runtime bodies. Before offline execution, supply "
        "the same authenticated files read-only at `/app/workload/inputs/files`; keep "
        "the manifest image-owned. Audit all image layers and metadata before publication; "
        "do not publish body-containing build contexts or caches.\n\n"
        if external_inputs else
        "This directory is the complete agent Docker context; it never contains verifier "
        "or held-out data. The Dockerfile's build-time guard authenticates small Git inputs "
        "and acquires larger public inputs from the exact upstream recipes in "
        "`payload-public.json`. Source/member bytes and every final input are checked "
        "against size/SHA-256 before use. Missing inputs and unavailable or "
        "changed upstream bytes fail closed with a clear diagnostic.\n\n"))
    if task.name == "sqlfmt-cli":
        input_context = input_context.replace(
            "Audit all image layers and metadata before publication;",
            "Audit all image layers and metadata;")
    published = reviewed_image_policy(task) == "ecr-after-signoff"
    image_policy = (
        ("The prebuilt image on Public ECR is pinned by digest in `task.toml`. " if published else
         "No prebuilt image is published for this task; build it from this directory. ")
        + ("Only the final input-free image is published, after an image-layer audit; "
           "build stages, caches and input-bearing images are never published. Supply the "
           "authenticated runtime inputs through the declared read-only mounts.\n"
           if declared_packet or external_inputs else
           "Images are published only after an image-layer audit; build stages and caches "
           "are never published.\n"))
    environment = "# Agent build context\n\n" + input_context + (
        "The pinned implementation is initialized as the asserted deterministic Git "
        "tree/root commit. Dependencies are acquired only during image construction. "
        "`workload/build.sh` rebuilds editable source offline and creates the thin "
        "`/app/build/run` wrapper; public local runs have no timer. All workload execution "
        "uses byte-identical shared runner code and `workload/task.py`.\n\n"
        + image_policy)
    return "\n".join(lines), environment


def catalog(tasks=None):
    tasks = task_paths(tasks)
    rows, ready = [], []
    statuses = {}
    for task in tasks:
        # Held Tasks stay packaged but are not part of the public release table.
        listed = reviewed_image_policy(task) != "held"
        unavailable = unavailable_inputs(task)
        if unavailable:
            statuses[task] = {"payload_status": "unavailable", "unavailable_inputs": unavailable}
            if listed:
                rows.append(f"| [{task.name}](tasks/{task.name}) | ? | unavailable | unavailable | unavailable |")
            continue
        public, heldout = missing(task, "public", allow_fetch=True), missing(
            task, "heldout", allow_unfetched_private=True)
        state = ("public-payload-missing" if public else
                 "heldout-missing" if heldout else "ready")
        status = json.loads((task / "qualification-status.json").read_text())
        status["payload_status"] = state
        statuses[task] = status
        if not public:
            ready.append(task.name)
        count = json.loads((task / "task.json").read_text()).get("example_count", "?")
        sizes = distribution(task)
        def size(value):
            return f"{value / 1_000_000:.2f} MB" if value >= 1_000_000 else f"{value:,} B"
        if listed:
            rows.append(f"| [{task.name}](tasks/{task.name}) | {count} | {size(sizes['git'])} | {size(sizes['upstream'])} | {TABLE_STATE[state]} |")
    table = "\n".join([START, "| Task | Public cases | Git input bodies | Upstream input bodies | Held-out inputs |",
                       "| --- | ---: | ---: | ---: | --- |", *rows, END])
    return tasks, ready, statuses, table


def generated(tasks=None):
    scoped = tasks is not None
    tasks, ready, statuses, table = catalog(tasks)
    available_tasks = [task for task in tasks if statuses[task]["payload_status"] != "unavailable"]
    coincidences = [] if scoped else output_coincidences(available_tasks)
    output = {}
    # Global aggregates require the explicit global scope and complete inputs.
    if not scoped and len(available_tasks) == len(tasks):
        readme = (ROOT / "README.md").read_text()
        before, rest = readme.split(START)
        _, after = rest.split(END)
        output[ROOT / "README.md"] = before + table + after
    for path, status in statuses.items():
        if status["payload_status"] == "unavailable":
            continue  # Never bind pointer text or rewrite an unavailable task.
        output[path / "instruction.md"] = agent_instructions(path)
        # instruction.md is the default rendering of this template (tools/render_instruction.py).
        output[path / PROMPT_TEMPLATE] = templatize(output[path / "instruction.md"], prompt_defaults(path))
        from judge.cpu_policy import release_policy
        release = json.loads((path / "release.json").read_text())
        release["cpu_policy"] = release_policy()
        if "cores" in release:
            release["cores"] = [8]  # Legacy field described timing, not subset correctness.
        output[path / "release.json"] = json.dumps(release, indent=2) + "\n"
        definition = json.loads((path / "task.json").read_text())
        corrected_contract = reported_memory_policy(definition.get("contract", ""))
        if corrected_contract != definition.get("contract", ""):
            definition["contract"] = corrected_contract
            output[path / "task.json"] = json.dumps(definition, indent=2) + "\n"
        judge_path = path / "tests/judge.json"
        lock_path = path / "tests/heldout.lock.json"
        if judge_path.is_file() and lock_path.is_file():
            settings = json.loads(judge_path.read_text())
            available = json.loads(lock_path.read_text()).get("status")
            if available in ("available", "unavailable") and settings.get("heldout_status") != available:
                settings["heldout_status"] = available
                output[judge_path] = json.dumps(settings, indent=2) + "\n"
        output[path / "environment/workload/runner.py"] = CANONICAL_RUNNER.read_text()
        output[path / "qualification-status.json"] = json.dumps(status, indent=2) + "\n"
        readme, environment_readme = task_readmes(path, status["payload_status"])
        output[path / "README.md"] = readme
        output[path / "environment/README.md"] = environment_readme
        provenance = json.loads((path / "provenance.json").read_text())
        provenance["public_input_distribution"] = distribution(path)
        matches = [row for row in coincidences if row["private_task"] == path.name]
        if scoped:
            pass  # Preserve prior global collision disclosures; this is not a global audit.
        elif matches:
            provenance["expected_output_coincidences"] = matches
        else:
            provenance.pop("expected_output_coincidences", None)
        bindings = {}
        histories = git_history_roots(path)
        for name in ("task.toml", "task.json", "instruction.md", "release.json"):
            content = output[path / name].encode() if path / name in output else (path / name).read_bytes()
            bindings[name] = hashlib.sha256(content).hexdigest()
        for base in ("environment", "tests"):
            for item in (path / base).rglob("*"):
                relative = item.relative_to(path)
                if (any(item.resolve().is_relative_to(root) for root in histories)
                        or not item.is_file() or item.is_symlink() or
                        any(part in ("receipts", "__pycache__", "inputs", "data") for part in relative.parts)):
                    continue
                content = output[item].encode() if item in output else item.read_bytes()
                bindings[relative.as_posix()] = hashlib.sha256(content).hexdigest()
        provenance["current_files_sha256"] = dict(sorted(bindings.items()))
        output[path / "provenance.json"] = json.dumps(provenance, indent=2) + "\n"
    for name, ids, description in ([] if scoped or len(available_tasks) != len(tasks) else [
        ("all", [task.name for task in tasks], "Every packaged task; payloads may be pending."),
        ("ready", ready, "Public-execution ready; held-out admission is separate, see payload_status.")]):
        output[ROOT / f"datasets/{name}.json"] = json.dumps({
            "schema_version": 1, "description": description, "tasks": ids}, indent=2) + "\n"
    return output


def refresh_bindings(tasks=None):
    """Refresh current package bindings without changing upstream inventories."""
    import re
    def fix(node, task, parent, package_map=False, public_metadata=False):
        if isinstance(node, list):
            return [fix(value, task, parent, public_metadata=public_metadata) for value in node]
        if not isinstance(node, dict):
            return node
        out = {key: fix(value, task, parent, key in (
            "files", "copied_files_sha256", "file_sha256", "current_files_sha256"),
            public_metadata=public_metadata)
               for key, value in node.items()
               if not (public_metadata and
                       (key == "tests/heldout.lock.json" or key.startswith("tests/heldout/")))}
        for key, value in list(out.items()):
            if not isinstance(value, str) or not re.fullmatch("[a-f0-9]{64}", value):
                continue
            # Bare names in source/project inventories (README.md, Cargo.toml,
            # etc.) refer to upstream files, NOT the task package. Only explicit
            # package-relative map keys and paired metadata file pointers move.
            candidates = ([task / key] if key.startswith(("environment/", "tests/", "public/"))
                          or package_map else [])
            if key.endswith("_sha256"):
                pointer = out.get(key.removesuffix("_sha256"))
                if isinstance(pointer, str):
                    candidates.extend([task / pointer, task / "environment" / pointer, parent / pointer])
            for candidate in candidates:
                if candidate.is_file() and candidate.name != "provenance.json":
                    with candidate.open("rb") as stream:
                        out[key] = hashlib.file_digest(stream, "sha256").hexdigest()
                    break
        return out
    for _ in range(4):
        for task in task_paths(tasks):
            if unavailable_inputs(task):
                continue
            for path in sorted(task.rglob("*.json")):
                if any(part in ("receipts", "inputs", "notices", "heldout")
                       for part in path.relative_to(task).parts):
                    continue
                if path.name not in ("provenance.json", "public-artifacts.json", "source.lock.json"):
                    continue
                before = path.read_text()
                data = json.loads(before)
                after = fix(data, task, path.parent,
                            public_metadata=path.name == "public-artifacts.json"
                            and path.is_relative_to(task / "environment"))
                if data != after:
                    path.write_text(json.dumps(after, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--refresh-bindings", action="store_true")
    parser.add_argument("--task", action="append", help="Only inspect/update this task; repeat for a subset")
    parser.add_argument("--all", action="store_true", help="Explicit repository-wide maintenance")
    args = parser.parse_args()
    if args.task and args.all:
        parser.error("choose --task or --all")
    if not args.task and not args.all and not args.check:
        parser.error("choose --task NAME for isolated maintenance or explicit --all")
    scope = task_paths(args.task) if args.task else None
    selected = task_paths(scope)
    unavailable = {path.name: unavailable_inputs(path) for path in selected}
    unavailable = {name: rows for name, rows in unavailable.items() if rows}
    if not args.check:
        normalize_distribution(scope)
        refresh_bindings(scope)
    stale = []
    for path, text in generated(scope).items():
        if args.check:
            if not path.exists() or path.read_text() != text:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    if stale:
        parser.exit(1, "Stale catalog; run python3 tools/update_catalog.py:\n" +
                    "\n".join(stale) + "\n")
    if not args.check:
        # Status changes can themselves be package-bound. Settle those references
        # after generating the new status, then bind the resulting source metadata.
        refresh_bindings(scope)
        for path, text in generated(scope).items():
            path.write_text(text)
    if unavailable:
        print(json.dumps({"status": "partial-unavailable", "unavailable_tasks": unavailable,
                          "unavailable_tasks_rewritten": False, "scored_qualification": False}, indent=2))
        parser.exit(2, "Catalog scope unavailable for the listed tasks; install git-lfs and recover only authorized declared paths. No unavailable task was rewritten.\n")


if __name__ == "__main__":
    main()
