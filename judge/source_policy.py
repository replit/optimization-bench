"""Complete source snapshots and protected-path-aware pristine-tree replay."""
import fnmatch
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path, PurePosixPath

def allowed(name, policy):
    if not isinstance(name,str) or not name or '\\' in name: return False
    path=PurePosixPath(name)
    if path.is_absolute() or any(part in ('..','.git','node_modules','__pycache__') for part in path.parts):return False
    if str(path)!=name or any(part in ('','.') for part in name.split('/')):return False
    if path.suffix not in policy['allowed_extensions'] and not any(extension.count('.') > 1 and name.endswith(extension) for extension in policy['allowed_extensions']):return False
    if policy.get('allowed_files') is not None and name not in policy['allowed_files']:return False
    # Exact production allowlists are authoritative. Ruff legitimately declares
    # crates/ruff_python_stdlib/src/sys/*.rs; the legacy C-header namespace guard
    # must not silently remove files from that immutable modify-only set.
    if policy.get('allowed_files') is None and any(part in ('openssl','bits','sys','linux','python3','node:') for part in path.parts):return False
    if path.name in {x+'.h' for x in ('assert','complex','ctype','errno','fenv','float','inttypes','iso646','limits','locale','math','setjmp','signal','stdalign','stdarg','stdatomic','stdbool','stddef','stdint','stdio','stdlib','stdnoreturn','string','tgmath','threads','time','uchar','unistd','wchar','wctype','zlib','zconf')}|{'sitecustomize.py','usercustomize.py'}:
        return False
    if not any(root=='.' or name.startswith(root.rstrip('/')+'/') for root in policy['source_roots']):return False
    return not any(fnmatch.fnmatchcase(name,p) or fnmatch.fnmatchcase(path.name,p) for p in policy['protected_paths'])

def snapshot(root, policy, original=None, trusted_pristine=False):
    root=Path(root);files={}
    for source_root in policy['source_roots']:
        folder=root/source_root
        if folder.is_symlink():raise ValueError('symlinked_source_root')
        if not folder.exists():continue
        for base, dirs, names in os.walk(folder,followlinks=False):
            for directory in dirs:
                if (Path(base)/directory).is_symlink():raise ValueError('source_directory_symlink')
            dirs[:]=[d for d in dirs if d not in ('.git','node_modules','__pycache__')]
            for filename in names:
                path=Path(base)/filename;name=path.relative_to(root).as_posix()
                if allowed(name,policy):
                    if path.is_symlink() or not path.is_file():raise ValueError('source_not_regular')
                    files[name]=path.read_text(encoding='utf8')
    validate(files,policy,original=original,trusted_pristine=trusted_pristine)
    return files

def validate(files, policy, original=None, trusted_pristine=False):
    if not isinstance(files,dict) or len(files)>policy['max_source_files']:raise ValueError('invalid_source_manifest')
    if policy.get('operations') == ['modify']:
        if not isinstance(policy.get('allowed_files'),list):
            raise ValueError('modify-only directory scope requires allowed_files')
        if set(files) != set(policy['allowed_files']):
            raise ValueError('modify_only_source_manifest')
    for name,value in files.items():
        if not allowed(name,policy):raise ValueError('source_scope_mismatch')
        if not isinstance(value,str) or '\x00' in value:raise ValueError('invalid_source_text')
        if name.endswith(('.c','.h','.hpp')) and not trusted_pristine:
            # A pinned upstream file may already have e.g. a CONFIG_OPENSSL
            # include. Preserve its original directive exactly, without
            # permitting candidates to add/alter dangerous directives.
            for pattern, reason in (
                (r'^\s*#\s*(?:define|undef)\s+(?:SHA256\w*|printf|fwrite|fopen|clock_gettime|getenv)\b[^\n]*',
                 'verifier_macro_tamper'),
                (r'^\s*#\s*include\s*[<"]openssl/[^\n]*', 'verifier_only_dependency')):
                current = Counter(re.findall(pattern,value,re.M))
                baseline = Counter(re.findall(pattern,(original or {}).get(name,''),re.M))
                if current - baseline:raise ValueError(reason)
    if sum(len(value.encode()) for value in files.values())>policy['max_source_bytes']:raise ValueError('source_too_large')

def digest(files):
    return hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def apply(root, original, files, policy):
    validate(files,policy,original=original);root=Path(root)
    for name in original.keys()-files.keys():
        (root/name).unlink()
    for name,value in files.items():
        if value==original.get(name):continue
        path=root/name
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p!=root and p.is_relative_to(root)):
            raise ValueError('symlink_replay_path')
        path.write_text(value,encoding='utf8')
    return {'added':sorted(files.keys()-original.keys()),'deleted':sorted(original.keys()-files.keys()),
            'modified':sorted(k for k in files.keys()&original.keys() if files[k]!=original[k])}
