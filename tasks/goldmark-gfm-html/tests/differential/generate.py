"""Verifier-only exact GFM replay plus documented parser/renderer API coverage."""


def generate(rng, budget):
    if budget <= 0:
        return
    # One bounded native API sequence covers the full configured boolean matrix,
    # reusable parser/context, independent parsers and mutation of returned ASTs.
    yield {"kind": "api", "sources": [
        "# heading\n\n- [x] checked\n\n~~strike~~ & <b>raw</b>\nline\nnext\n",
        "a | b\n--|--\n1 | 2\n\nhttps://example.org\n\n[ref]: /target\n[ref]\n",
        "# heading\n\n[missing]\n\n```\nunterminated\x00\n",
    ]}
    fragments = [
        "", "# title\n", "plain & < > \" '\n", "λ 中文 🙂 e\u0301\n",
        "[label](javascript:alert(1))\n", "<script>x</script>\n",
        "- [ ] open\n- [x] done\n", "~~strike~~ *em* **strong**\n",
        "a|b\n:-|-:\nx|y\n", "> quote\n>\n> - nested\n",
        "~~~c\nint main(void) {}\n~~~\n", "[x][ref]\n\n[ref]: /a \"title\"\n",
        "www.example.org user@example.org\n", "``bad ` code\n\x00\x01\n",
        "![alt](missing \"unterminated)\n", "1. one\n   - sub\n\n---\n",
    ]
    for index in range(1, min(budget, 16)):
        source = "# preserved heading\n\n" if index == 1 else ""
        source += "".join(rng.choice(fragments) for _ in range(1 + index % 5))
        # Real originals are read inside the network-disabled task image,
        # selected deterministically and included completely up to a fixed bound.
        yield {
            "kind": "convert", "source": source,
            "real_fixture_index": rng.randrange(95) if index % 2 == 0 else None,
            "real_slice": rng.randrange(4), "repeat": 3,
            "newline": ["lf", "crlf", "cr"][index % 3],
            "invalid_suffix_hex": "fffe80" if index % 4 == 0 else "",
        }


def shrink(case):
    if case["kind"] == "api":
        yield {"kind": "api", "sources": ["# h\n"]}
        yield {"kind": "api", "sources": [""]}
        return
    for source in ("# h\n", "&\n", "x\n", ""):
        yield {**case, "source": source, "real_fixture_index": None,
               "repeat": 1, "invalid_suffix_hex": "", "newline": "lf"}
