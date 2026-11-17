"""Public authored Ruff API/control inputs and commands; no project identities."""
import json
from pathlib import Path
def prepare(root):
 root.mkdir(exist_ok=True)
 fixture='''import os, sys
import json
from typing import List

def f(unused: int, x: List[int] = []):
    local = 1
    try:
        assert x == None
    except Exception:
        pass
    return eval("1+1")

x = 1  # noqa: F401
'''
 (root/'api.py').write_text(fixture)
 (root/'syntax.py').write_text('type Alias[T] = list[T]\ndef f[T](x: T) -> T:\n    match x:\n        case None:\n            return x\n    return x\n')
 (root/'suppression.py').write_text('import os  # noqa: F401\nimport sys\nimport json\n')
 (root/'broken.py').write_text('def broken(:\n    pass\n')
 (root/'unicode.py').write_text('é = 1\nimport os\n')
 (root/'api.pyi').write_text('from typing import List\ndef f(x: List[int] = []) -> None: ...\n')
 (root/'api.ipynb').write_text(json.dumps({'cells':[{'cell_type':'code','execution_count':None,'metadata':{},'outputs':[],'source':['import os\n','x = 1\n']}],'metadata':{},'nbformat':4,'nbformat_minor':5}))
 (root/'bad.toml').write_text('[lint\nselect = ["F"]\n')
 (root/'ruff.toml').write_text('target-version="py37"\n[lint]\nselect=["RUF201"]\npreview=true\n')
 (root/'nested').mkdir();(root/'nested/ruff.toml').write_text('[lint]\nselect=["F401"]\nper-file-ignores={"ignored.py"=["F401"]}\n')
 for n in ['nested/finding.py','nested/ignored.py']:(root/n).write_text('import os\n')
 cases=[('version',['--version']),('rules',['rule','--all','--output-format=json']),('configuration',['config','--output-format=json']),('help',['check','--help'])]
 base=['check','--no-cache','--no-fix','--output-format=json']
 for minor in range(7,16):
  for preview in [False,True]:cases.append((f'python-3{minor}-preview-{preview}',base+['--isolated','--select','ALL','--target-version','py3'+str(minor)]+(['--preview']if preview else[])+['api.py','syntax.py','api.pyi','api.ipynb']))
 cases += [('suppression',base+['--isolated','--select','F','suppression.py']),('ignore-noqa',base+['--isolated','--select','F','--ignore-noqa','suppression.py']),('syntax-error',base+['--isolated','broken.py']),('unicode',base+['--isolated','--select','F','unicode.py']),('invalid-config',base+['--config','bad.toml','api.py']),('missing-file',base+['--isolated','missing.py']),('config-lint',base+['--preview','ruff.toml']),('nested-config',base+['nested']),('excluded',base+['--isolated','--exclude','api.py','--force-exclude','api.py']),('show-settings',['check','--isolated','--show-settings','api.py'])]
 return cases
