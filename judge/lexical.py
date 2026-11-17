"""Grammar-backed lexical masking, never a cross-language quoting heuristic.

Tree-sitter grammars are hash-locked verifier dependencies. Missing parsers fail
closed. Python character offsets and newlines are preserved, including Unicode.
Template/f-string executable substitutions remain visible to runtime-state scans.
This is not a type checker or a complete semantic ABI proof.
"""
import bisect
import functools
import importlib

SUFFIXES = {
    ".rs": "rust", ".go": "go", ".js": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "tsx", ".java": "java",
    ".py": "python", ".c": "c", ".h": "cpp", ".cc": "cpp", ".cpp": "cpp",
    ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp",
}


@functools.lru_cache(maxsize=None)
def language(name):
    from tree_sitter import Language
    module_name = "typescript" if name == "tsx" else name
    module = importlib.import_module("tree_sitter_" + module_name)
    function = {"typescript": "language_typescript", "tsx": "language_tsx"}.get(name, "language")
    return Language(getattr(module, function)())


def parse(text, suffix):
    from tree_sitter import Parser
    if suffix not in SUFFIXES:
        raise ValueError("No lexical grammar configured for " + suffix)
    data = text.encode("utf-8")
    return Parser(language(SUFFIXES[suffix])).parse(data), data


def signature_text(node, data):
    """Canonical tokens: ignore formatting/comments, preserve literal contents."""
    tokens, pending = [], [node]
    literals = {"string", "string_literal", "raw_string_literal", "char_literal",
                "character_literal", "regex", "template_string", "rune_literal"}
    while pending:
        current = pending.pop()
        if current.type == "comment" or current.type.endswith("_comment"):
            continue
        if not current.children or current.type in literals:
            tokens.append(data[current.start_byte:current.end_byte].decode())
        else:
            pending.extend(reversed(current.children))
    return " ".join(tokens)


def code_only(text, suffix):
    tree, _ = parse(text, suffix)
    chars, offsets = list(text), [0]
    for char in text:
        offsets.append(offsets[-1] + len(char.encode("utf-8")))

    def mask(node):
        start = bisect.bisect_left(offsets, node.start_byte)
        end = bisect.bisect_left(offsets, node.end_byte)
        for index in range(start, end):
            if chars[index] not in "\r\n":
                chars[index] = " "

    strings = {
        "string_literal", "raw_string_literal", "char_literal",
        "interpreted_string_literal", "raw_string_literal", "rune_literal",
        "character_literal", "regex", "jsx_text", "hash_bang_line",
    }
    # JS/TS templates and Python f-strings contain code: mask only their literal
    # children, not substitutions. Ordinary strings have no executable children.
    pending = [tree.root_node]
    while pending:
        node = pending.pop()
        kind = node.type
        if kind == "comment" or kind.endswith("_comment") or kind in strings:
            mask(node)
            continue
        if kind in ("string", "template_string"):
            for child in node.children:
                if child.type in ("interpolation", "template_substitution"):
                    pending.append(child)
                else:
                    mask(child)
            continue
        pending.extend(node.children)
    return "".join(chars)
