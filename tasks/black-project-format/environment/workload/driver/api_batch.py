"""Protected resident driver for Black API batches (black-api-case-v1).

The request holds one or more authenticated documents of one kind, processed in manifest order:
snippets: black.format_str(code, mode=Mode(...)) per documentation fragment, as blacken-docs and
          mkdocstrings call it; any exception is recorded per fragment and the batch continues.
ranges:   black.format_file_contents(text, fast=False, mode=Mode(), lines=ranges) per editor
          range request, as python-lsp-black calls it; NothingChanged is recorded as null.

Untimed: read the request, import Black, build Mode objects. Then SIGSTOP (ready); the runner
records t0 on SIGCONT. Timed: the serial call loop over every document only. Then SIGSTOP
(result); t1. Untimed: encode and write one complete result per document. Results are never
memoized across calls.
Usage: api_batch.py REQUEST.json OUTPUT_DIR
"""
import json
import os
import signal
import sys


def main():
    request_path, output_dir = sys.argv[1], sys.argv[2]
    with open(request_path, encoding="utf-8") as handle:
        docs = json.load(handle)["docs"]
    import black
    kinds = {doc["kind"] for doc in docs}
    if len(kinds) != 1 or not kinds <= {"snippets", "ranges"}:
        raise ValueError("API request needs documents of one known kind")
    kind = kinds.pop()
    work, owners, modes = [], [], {}
    for index, doc in enumerate(docs):
        if kind == "snippets":
            for item in doc["fragments"]:
                m = item["mode"]
                key = json.dumps(m, sort_keys=True)
                if key not in modes:
                    modes[key] = black.Mode(
                        target_versions={black.TargetVersion[v] for v in m["target_versions"]},
                        line_length=m["line_length"], string_normalization=m["string_normalization"],
                        is_pyi=m["is_pyi"], preview=m["preview"])
                work.append((item["code"], modes[key]))
                owners.append(index)
        else:
            for item in doc["requests"]:
                work.append((item["src"], [tuple(r) for r in item["lines"]]))
                owners.append(index)
    results = []
    append = results.append
    if kind == "snippets":
        format_str = black.format_str
        os.kill(os.getpid(), signal.SIGSTOP)
        for code, mode in work:
            try:
                append(format_str(code, mode=mode))
            except Exception as error:  # recorded per fragment, as blacken-docs does
                append([type(error).__name__, str(error)])
        os.kill(os.getpid(), signal.SIGSTOP)
    else:
        mode = black.Mode()
        format_file_contents, nothing_changed = black.format_file_contents, black.NothingChanged
        os.kill(os.getpid(), signal.SIGSTOP)
        for text, lines in work:
            try:
                append(format_file_contents(text, fast=False, mode=mode, lines=lines))
            except nothing_changed:
                append(None)
            except Exception as error:
                append([type(error).__name__, str(error)])
        os.kill(os.getpid(), signal.SIGSTOP)
    per_doc = [[] for _ in docs]
    for owner, result in zip(owners, results):
        per_doc[owner].append(result)
    for index, doc in enumerate(docs):
        payload = {"format": "black-api-result-v1", "id": doc["id"], "kind": kind, "results": per_doc[index]}
        with open(os.path.join(output_dir, "result-%d.json" % index), "wb") as handle:
            handle.write(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


if __name__ == "__main__":
    main()
