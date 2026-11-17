"""Deterministic public Black API cases; no expected answers."""
import itertools


def generate(rng, budget):
    modes = list(itertools.product([40, 88, 120], [False, True], [False, True]))
    examples = [
        "", "x=1\n", "# café λ🙂\nx={'é': [1,2,3]}\n",
        "def f(a:int=1,/,*args,**kwargs):\n return (a,args,kwargs)\n",
        "match x:\n case {'a': value}: print(value)\n",
        "type Vector[T] = list[T]\n", "x = 'unterminated\n",
        "def f(:\n pass\n", "x='\x00'\n", "# fmt: off\nx=  1\n# fmt: on\n",
        "async def f():\n return [x async for x in stream if x]\n",
        "class X:\n def f(self): ...\n",
    ]
    for index in range(budget):
        length, preview, normalize = modes[index % len(modes)]
        text = examples[index % len(examples)]
        if index == 16:
            text = '{"cells":[{"cell_type":"code","source":["%%custom\\n","x=1\\n"],"metadata":{},"outputs":[],"execution_count":null}],"metadata":{"language_info":{"name":"python"}},"nbformat":4,"nbformat_minor":5}'
        if index == budget - 1:
            text = "values=[" + ",".join(repr("λ" * 20) for _ in range(256)) + "]\n"
        yield {
            "source": text,
            "mode": {"line_length": length, "preview": preview,
                     "string_normalization": normalize,
                     "is_pyi": index % 4 == 0, "unstable": index % 5 == 0,
                     "is_ipynb": index == 16,
                     "skip_source_first_line": index % 7 == 0,
                     "python_cell_magics": ["custom"] if index == 16 else [],
                     "enabled_features": [
                         ["string_processing", "hug_parens_with_braces_and_square_brackets",
                          "wrap_comprehension_in", "simplify_power_operator_hugging",
                          "wrap_long_dict_values_in_parens", "fix_if_guard_explosion_in_case_statement",
                          "pyi_overload_group_blank_lines", "pyi_blank_line_before_decorated_class"][index % 8]
                     ] if index >= 8 else [],
                     "magic_trailing_comma": index % 2 == 0,
                     "target": ["PY39", "PY310", "PY311", "PY312", "PY313", "PY314"][index % 6]},
            "real_index": rng.randrange(500) if index % 3 == 0 else None,
            "mutation": rng.choice(["append", "unicode_comment", "indent_error"]),
            "fast": index % 2 == 0,
            "ranges": [[1, 2]] if index % 4 == 1 else [],
        }


def shrink(case):
    yield {**case, "real_index": None, "source": case["source"][:len(case["source"]) // 2]}
    yield {**case, "real_index": None, "source": "# header\nx='a'\n", "ranges": []}
    yield {**case, "real_index": None, "source": "x='a'\n", "ranges": []}
    yield {**case, "real_index": None, "source": "x=1\n", "ranges": []}
    yield {**case, "real_index": None, "source": "", "ranges": []}
