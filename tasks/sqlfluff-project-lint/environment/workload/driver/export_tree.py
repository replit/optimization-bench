"""Protected workload client. Export SQLFluff syntax, never compute lineage."""
import json
from pathlib import Path
import sys
import traceback

import sqlfluff
from sqlfluff.core import FluffConfig, Linter

SETTINGS = {
    "dialect": "bigquery", "templater": "raw",
    "large_file_skip_byte_limit": 0, "large_file_skip_char_limit": 0,
}
TREE_OPTIONS = {
    "code_only": False, "show_raw": True, "include_meta": True, "include_position": True,
}


def diagnostic(error):
    return {
        "type": f"{type(error).__module__}.{type(error).__qualname__}",
        "record": error.to_dict(), "message": str(error),
        "fatal": error.fatal, "ignore": error.ignore,
    }


def main(paths):
    linter = Linter(config=FluffConfig(overrides=SETTINGS, ignore_local_config=True))
    records = []
    failed = False
    for path in paths:
        record = {"path": path}
        try:
            # Preserve original bytes and let the real API handle newline normalization.
            text = Path(path).read_bytes().decode("utf-8")
            parsed = linter.parse_string(text, fname=path)
            record["source_text"] = parsed.source_str
            record["templating_violations"] = [diagnostic(e) for e in parsed.templating_violations]
            record["variants"] = [{
                "templated_text": variant.templated_file.templated_str,
                "tree": variant.tree.to_tuple(**TREE_OPTIONS) if variant.tree else None,
                "lexing_violations": [diagnostic(e) for e in variant.lexing_violations],
                "parsing_violations": [diagnostic(e) for e in variant.parsing_violations],
            } for variant in parsed.parsed_variants]
            failed |= bool(parsed.templating_violations) or any(
                v.lexing_violations or v.parsing_violations for v in parsed.parsed_variants
            )
        except Exception as error:
            failed = True
            record["exception"] = {
                "type": f"{type(error).__module__}.{type(error).__qualname__}",
                "message": str(error), "traceback": traceback.format_exc(),
            }
        records.append(record)
    output = {
        "format": "sqlfluff-complete-syntax-v1",
        "sqlfluff_version": sqlfluff.__version__,
        "settings": SETTINGS, "tree_options": TREE_OPTIONS, "files": records,
    }
    sys.stdout.buffer.write(
        (json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
