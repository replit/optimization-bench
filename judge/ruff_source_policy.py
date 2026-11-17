"""Immutable modify-only Rust scope, including protected inline tests and docs."""
import difflib,hashlib,json,re
from pathlib import Path

TEST_START=re.compile(r'^\s*(?:#\[(?:cfg\([^\n]*\btest\b|(?:\w+::)?(?:test|test_case|rstest)\b|cfg_attr\(test)|(?:pub\s+)?mod\s+tests\b)',re.M)
# Frozen byte-commitment format, not an API/runtime lexical masker. Changing
# these spans would require an explicit migration of the pinned Ruff policy.
DOCS=re.compile(r'(?m)^[ \t]*//[/!].*(?:\n|$)|/\*[*!][\s\S]*?\*/')
def sha_text(s):return hashlib.sha256(s.encode()).hexdigest()
def documentation(s):return [m.group()for m in DOCS.finditer(s)]
def commitment(s):
 m=TEST_START.search(s)
 return {'tail_chars':len(s)-m.start()if m else 0,'tail_sha256':sha_text(s[m.start():])if m else None,
         'documentation_sha256':sha_text(json.dumps(documentation(s),ensure_ascii=False))}
def validate_inline(files,policy,original=None):
 if original is not None:
  validate_inline(original,policy)
  for name,text in files.items():
   before=original[name];rule=policy['inline_protection'][name];n=rule['tail_chars']
   if n:
    start=TEST_START.search(text)
    if start is None:raise ValueError('protected_inline_tests_changed:'+name)
    old=before[-n:].splitlines(keepends=True);new=text[start.start():].splitlines(keepends=True)
    if any(tag not in ('equal','insert') for tag,*_ in difflib.SequenceMatcher(None,old,new,autojunk=False).get_opcodes()):
     raise ValueError('protected_inline_tests_changed:'+name)
   old_docs=documentation(before);new_docs=documentation(text)
   if any(tag not in ('equal','insert') for tag,*_ in difflib.SequenceMatcher(None,old_docs,new_docs,autojunk=False).get_opcodes()):
    raise ValueError('protected_documentation_or_doctest_changed:'+name)
  return
 for name,text in files.items():
  rule=policy['inline_protection'][name];n=rule['tail_chars']
  if n and (len(text)<n or sha_text(text[-n:])!=rule['tail_sha256']):raise ValueError('protected_inline_tests_changed:'+name)
  if sha_text(json.dumps(documentation(text),ensure_ascii=False))!=rule['documentation_sha256']:raise ValueError('protected_documentation_or_doctest_changed:'+name)
def snapshot(root,policy):
 root=Path(root);files={}
 for name in policy['allowed_files']:
  p=root/name
  if not p.is_file()or p.is_symlink()or any(q.is_symlink()for q in p.parents if q!=root and q.is_relative_to(root)):raise ValueError('nonregular_source')
  files[name]=p.read_text()
 validate_inline(files,policy)
 if sum(len(v.encode())for v in files.values())>policy['max_source_bytes']:raise ValueError('source_size_limit')
 return files
