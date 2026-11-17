"""Small independent API probes, outside timed workloads and upstream fixtures."""
import json
import sys

results = []

def record(name, call):
    try:
        results.append({'case': name, 'value': call()})
    except Exception as exc:
        results.append({'case': name, 'error': type(exc).__name__, 'message': str(exc)})

if sys.argv[1] == 'black-project-format':
    import black
    sources = [
        'x=1\n', 'def f(a:int,b:str="hello")->None:\n return(a,b)\n',
        'result = call(first_argument, second_argument, third_argument, fourth_argument)\n',
        '# fmt: off\nx=  [1,2,   3]\n# fmt: on\ny=2\n',
        'match value:\n case {"x": x, "y": y}:\n  result=(x,y)\n',
        'def broken(:\n', 'text = "héllo 🌏"\n',
        'values = [x*x for x in range(10) if x%2]\n',
        'def f(a: int, b: str) -> None: ...\n',
    ]
    for i, source in enumerate(sources):
        for width in (40, 79, 88):
            mode = black.Mode(line_length=width, is_pyi=i == 8)
            record(f'format-{i}-{width}', lambda s=source, m=mode: black.format_str(s, mode=m))
    record('range-format', lambda: black.format_str('x=1\ny=2\nz=3\n', mode=black.Mode(), lines=((2, 2),)))
elif sys.argv[1] == 'sqlfluff-project-lint':
    import sqlfluff
    sources = [
        ('ansi', 'select a,b from t where a=1'),
        ('postgres', 'SELECT payload->>\'name\' AS name FROM events;\n'),
        ('bigquery', 'SELECT x FROM UNNEST([1, 2, 3]) AS x;\n'),
        ('ansi', 'SELECT {{ unknown_value }} FROM t;\n'),
        ('ansi', 'SELECT ( FROM t;'),
        ('ansi', 'select a from t -- noqa: CP01\n'),
    ]
    for i, (dialect, source) in enumerate(sources):
        record(f'lint-{i}', lambda s=source, d=dialect: sqlfluff.lint(s, dialect=d))
        record(f'fix-{i}', lambda s=source, d=dialect: sqlfluff.fix(s, dialect=d))
else:
    raise ValueError('Unknown control task')
print(json.dumps(results, sort_keys=True))
