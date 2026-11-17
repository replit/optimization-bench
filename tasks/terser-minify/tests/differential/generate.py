"""JS grammar and public TreeWalker stateful callbacks."""
def generate(rng, budget):
    yield {"kind": "walker", "source": "function f(a){return a+1} f(2);", "callback": "default"}
    # Complete configured boolean-option matrix, not random coverage of it.
    import itertools
    emitted = 1
    for compress, mangle, module, beautify in itertools.product([False, True], repeat=4):
        if emitted >= budget:
            return
        yield {"kind": "minify", "source": "function f(x){return x+1} f(2);",
               "options": {"compress": compress, "mangle": mangle, "module": module,
                           "format": {"beautify": beautify}}}
        emitted += 1
    for i in range(max(0, budget - emitted)):
        source = rng.choice(["", "let λ='🙂';", "function f(x){return x+1} f(2);",
                             "const x = [1,2,3].map(x=>x*2);", "if (", "'unterminated",
                             "/*comment*/let x = " + str(rng.randrange(10000)) + ";"])
        if i == budget - emitted - 1:
            source = "const values=[" + ",".join(str(n) for n in range(4096)) + "];"
        if i % 3 == 0:
            yield {"kind": "walker", "source": source, "callback": rng.choice(["default", "rest", "normal"])}
        else:
            yield {"kind": "minify", "source": source, "options": {
                "compress": bool(rng.randrange(2)), "mangle": bool(rng.randrange(2)),
                "module": bool(rng.randrange(2)), "format": {"beautify": bool(rng.randrange(2))}}}


def shrink(case):
    for source in ["a()", "1", ""]:
        yield {**case, "source": source}
