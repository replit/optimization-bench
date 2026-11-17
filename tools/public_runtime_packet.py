"""Prepare declared read-only public data for an ordinary agent environment."""
import argparse
import json
from pathlib import Path
import re
import shlex
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from judge import public_input_preflight, public_packet, score


def prepare(task, image, cache, output):
    task, cache, output = Path(task).resolve(), Path(cache).resolve(), Path(output).resolve()
    declaration = public_packet.load(task)
    if declaration is None:
        raise ValueError("This helper requires an explicit public-input-packet.json; ordinary bundled images are unchanged")
    public_input_preflight.ensure_public_inputs(task)
    output.mkdir(parents=True, exist_ok=False)
    image = score.ensure_image(task, image, output)
    trusted = score.assets(task, image, cache)
    receipt = public_input_preflight.ensure_public_inputs(task, source=trusted / "public-workload")
    mounts = []
    for relative in declaration["mounts"]:
        source = trusted / "public-workload" / relative
        if not source.exists():
            raise ValueError("Declared agent-visible public data root is missing: " + relative)
        mounts.append({"type": "bind", "source": str(source), "target": "/app/workload/" + relative,
                       "read_only": True, "bind": {"create_host_path": False}})
    wrapper = output / "harbor-task"
    environment = wrapper / "environment"
    environment.mkdir(parents=True)
    (wrapper / "instruction.md").write_bytes((task / "instruction.md").read_bytes())
    tests = wrapper / "tests"
    tests.mkdir()
    (tests / "test.sh").write_text(
        "#!/bin/sh\n"
        "echo 'Agent-only packet: export source and run the canonical judge.score separately.' >&2\n"
        "exit 2\n")
    config = (task / "task.toml").read_text()
    start = re.search(r"(?m)^\[environment\]\s*$", config)
    if start is None:
        raise ValueError("Task has no original environment resource policy")
    tail = config[start.end():]
    following = re.search(r"(?m)^\[", tail)
    end = start.end() + (following.start() if following else len(tail))
    section = config[start.end():end]
    section = re.sub(r"(?m)^docker_image\s*=.*\n?", "", section)
    config = config[:start.end()] + "\ndocker_image = " + json.dumps(image) + "\n" + section + config[end:]
    original = tomllib.loads((task / "task.toml").read_text())
    updated = tomllib.loads(config)
    expected = {**original, "environment": {**original["environment"], "docker_image": image}}
    if updated != expected:
        raise ValueError("Agent packet wrapper changed a protected task setting")
    (wrapper / "task.toml").write_text(config)
    (environment / "Dockerfile").write_text("FROM " + image + "\n")
    compose = {"services": {"main": {"volumes": mounts}}}
    (environment / "docker-compose.yaml").write_text(json.dumps(compose, indent=2) + "\n")
    docker_arguments = []
    for mount in mounts:
        docker_arguments.extend(["--mount", public_packet.mount(mount["source"], mount["target"])])
    packet = {"schema": "optimbench-agent-public-runtime-packet-v1", "task": task.name,
              "image": image, "declaration_sha256": declaration["sha256"], "scope": "public",
              "mounts": mounts, "docker_arguments": docker_arguments,
              "pristine": str(trusted / "pristine"),
              "harbor_task": str(wrapper), "public_preflight": receipt,
              "verification": "agent-only; export source and run canonical judge.score separately"}
    (output / "packet.json").write_text(json.dumps(packet, indent=2) + "\n")
    command = ["harbor", "run", "--path", str(wrapper), "--disable-verification",
               "--agent", "YOUR_AGENT", "--model", "YOUR_MODEL"]
    (output / "agent-command.txt").write_text(shlex.join(command) + "\n")
    return packet


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--image", help="Existing protected task image; default builds the original recipe")
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    task = ROOT / "tasks" / args.task
    print(json.dumps(prepare(task, args.image, args.cache, args.out), indent=2))


if __name__ == "__main__":
    main()
