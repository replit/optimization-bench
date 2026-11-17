"""Small public API domain for the wrap-up RC; not full surface qualification."""
import hashlib
import json

TASK = "sqlfmt-cli"
FORMAT = "github.com/dimitri/sqlfmt/format.Format"
LEX = "github.com/dimitri/sqlfmt/format.Lex"

def digest(case):
    return hashlib.sha256(json.dumps({k:v for k,v in case.items() if k != "case_sha256"},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()

def make(sql, mode="format", chunk=0, faults=None):
    surface = LEX if mode == "lex_alias" else FORMAT
    case = {"task": TASK, "surface_id": surface,
            "contract_id": "sqlfmt.public.lex-independent-results" if mode == "lex_alias" else "sqlfmt.public.reader-format",
            "precondition_id": "sqlfmt.owned-bytes-and-legal-readers",
            "input": {"sql_utf8": sql}, "options": {"mode": mode, "chunk": chunk},
            "sequence": ["construct", "call", "retain", "interleave", "fresh"] if mode == "lex_alias" else ["construct", "read-to-result"],
            "faults": faults or []}
    case["case_sha256"] = digest(case)
    return case

def surface(case):
    return {"id": case["surface_id"], "visibility": "public", "contract_id": case["contract_id"],
            "precondition_id": case["precondition_id"],
            "oracle": {"required_error_fields": {"class": "nonempty_string"}},
            "contract_sources": ["upstream 8c676b5 format/format.go: Format(io.Reader)",
                                 "upstream 8c676b5 format/lexer.go: Lex([]byte) and Token.Text"]}

def precondition(case, authority):
    valid = (case.get("task") == TASK and case.get("surface_id") in (FORMAT, LEX)
             and case.get("case_sha256") == digest(case)
             and type(case.get("input", {}).get("sql_utf8")) is str)
    if not valid:
        return {"valid": False, "reason": "Wrong public domain or self-commitment"}
    sql = case["input"]["sql_utf8"]
    try:
        size = len(sql.encode())
    except UnicodeEncodeError:
        return {"valid": False, "reason": "Unsupported surrogate transport"}
    options = case.get("options", {})
    mode = options.get("mode")
    expected = make(sql, mode, options.get("chunk"), case.get("faults"))
    valid = (size <= 262144 and mode in ("format", "lex_alias")
             and type(options.get("chunk")) is int and 0 <= options["chunk"] <= 4096
             and len(case.get("faults", [])) <= 1
             and all(type(f.get("after")) is int and 0 <= f["after"] <= size
                     and f.get("kind") in ("EINTR", "EIO") for f in case.get("faults", []))
             and (mode != "lex_alias" or not case["faults"])
             and expected == case)
    return {"valid": valid, "reason": "Public owned byte slices and legal io.Reader short/error schedules" if valid else "Outside bounded public reader/token domain"}

def cases():
    motif = "select a,b from t where x=1;\n"
    positive = [make(""), make(motif, chunk=1), make(motif * 4800, chunk=4096)]
    stream = positive + [make(motif, chunk=3, faults=[{"kind": kind, "after": after}])
                         for kind in ("EIO", "EINTR") for after in (0, 5)]
    stream.append(make("select 'unterminated", chunk=2))
    aliases = [make(text, mode="lex_alias") for text in (motif, "select 'λ', $$body$$, 123;")]
    return stream, positive, aliases
