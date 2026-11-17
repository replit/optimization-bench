"""Real public SQL recombinations across all formatter flag combinations."""
import hashlib
import io
import itertools
import json
from pathlib import Path
import tarfile


def public_sql(inputs):
    real = []
    manifest = json.loads((inputs / "manifest.json").read_text())
    documents = {doc["sha256"]: doc for split in manifest["splits"].values()
                 for batch in split for doc in batch["docs"]}
    for digest, document in sorted(documents.items()):
        raw = (inputs / "files" / digest).read_bytes()
        if len(raw) != document["size"] or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Public CLI input checksum mismatch")
        header, argv, transport, outputs, body = raw.split(b"\n", 4)
        if header != b"CLI1":
            raise ValueError("Unexpected public SQL payload")
        if transport == b"tar":
            with tarfile.open(fileobj=io.BytesIO(body)) as archive:
                for member in archive.getmembers():
                    if member.isfile() and member.name.endswith(".sql") and member.size <= 16000:
                        real.append(archive.extractfile(member).read().decode("utf-8", errors="replace"))
        elif len(body) <= 16000:
            real.append(body.decode("utf-8", errors="replace"))
    if not real:
        raise ValueError("No frozen original SQL")
    return real


def generate(rng, budget):
    real = public_sql(Path(__file__).parents[2] / "environment/workload/inputs")
    modes = [list(flags) for size in range(4)
             for flags in itertools.combinations(["-l", "-d", "-w"], size)]
    edges = ["", "SELECT", "select 'unterminated", "select (1;",
             "-- λ🙂 café\nselect 'é', NULL, 1+2;",
             "/* multiline\ncomment */ SELECT a,b FROM t WHERE a IS NOT NULL;",
             "select $$dollar ' quoted λ$$;", "select 1;\r\nselect 2;\r\n"]
    for index in range(budget):
        text = edges[index % len(edges)]
        # Keep malformed and empty cases separate, plus genuine file recombinations.
        if index >= 8:
            text = rng.choice(real) + "\n;\n" + text + "\n" + rng.choice(real)
        if index == budget - 1:
            text = "select " + ",".join(f"{i} as c{i}" for i in range(512)) + ";"
        yield {"sql": text, "other": rng.choice(real), "flags": modes[index % 8],
               "transport": ["stdin", "file", "tree"][index // 8 % 3],
               "special": ["-V", "-version", "-h", "--unknown", "missing.sql"][index % 5]}


def shrink(case):
    for sql in [case["sql"][:len(case["sql"]) // 2], "select 'λ';", "select 1;", ""]:
        yield {**case, "sql": sql, "other": ""}
