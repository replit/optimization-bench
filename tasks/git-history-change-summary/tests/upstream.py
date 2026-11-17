#!/usr/bin/env python3
"""Run unchanged selected original suites; preserve every TAP identity and outcome."""
import hashlib,json,os,re,shutil,stat,subprocess
from pathlib import Path
root=Path("/app/build/project")
work=Path("/scratch/upstream-work")
shutil.copytree(root,work,symlinks=True)
# The five original diff suites, then each later unit's original suites.
ORIGINAL=["t4000-diff-format.sh","t4002-diff-basic.sh","t4033-diff-patience.sh",
          "t4048-diff-combined-binary.sh","t4050-diff-histogram.sh"]
ADDED=["t4015-diff-whitespace.sh","t4055-diff-context.sh","t6403-merge-file.sh",
       "t6427-diff3-conflict-markers.sh"]
suites=ORIGINAL+ADDED
# Environment-dependent in this image (submodule add in the test environment).
SKIP="t4015.129"
# Pristine outcome ledger, including the image's environment failures: every
# identity and outcome must stay exactly unchanged (not merely "passed").
EXPECTED_IDENTITIES=334
EXPECTED_LEDGER="ceaeb5bcea0310fcc43830288fb855493044e76791a17f06de9339735e1e4521"
ORIGINAL_LEDGER="78e4d8a224b34b56ef4094089ba8a7b29412e3fd31a186a010401e851910af49"
assert all((work/"t"/s).is_file() for s in suites)
env={**os.environ,"LC_ALL":"C.UTF-8","TZ":"UTC","GIT_CONFIG_NOSYSTEM":"1",
     "GIT_CONFIG_GLOBAL":"/dev/null","GIT_ATTR_NOSYSTEM":"1","GIT_OPTIONAL_LOCKS":"0"}

def inventory(directory=work, exclude_options=True):
 result={}
 for path in sorted(directory.rglob("*")):
  name=path.relative_to(directory).as_posix()
  if exclude_options and name=="GIT-BUILD-OPTIONS":continue
  mode=stat.S_IMODE(path.lstat().st_mode)
  if path.is_symlink():result[name]=[mode,"symlink",os.readlink(path)]
  elif path.is_dir():result[name]=[mode,"directory"]
  elif path.is_file():
   with path.open("rb") as stream:digest=hashlib.file_digest(stream,"sha256").hexdigest()
   result[name]=[mode,path.stat().st_size,digest]
 return hashlib.sha256(json.dumps(result,sort_keys=True,separators=(",",":")).encode()).hexdigest()

# Only refresh upstream-generated path metadata after relocation. No production
# object, executable, source, test or fixture may change during this operation.
arm_source_inventory=inventory(root,False)
copied_inventory=inventory(work,False)
assert arm_source_inventory==copied_inventory,"Suite copy is not the build under test"
before=inventory()
old=(work/"GIT-BUILD-OPTIONS").read_text()
command=["make","CFLAGS=-O3 -g","NO_TCLTK=YesPlease","GIT-BUILD-OPTIONS"]
prep=subprocess.run(command,cwd=work,env=env,capture_output=True,text=True,timeout=120)
assert prep.returncode==0,prep.stdout+prep.stderr
after=inventory()
assert before==after,"Test preparation unexpectedly rebuilt or changed the build tree"
new=(work/"GIT-BUILD-OPTIONS").read_text()
def fields(text):return dict(re.findall(r"(?m)^([A-Z_]+)='(.*)'$",text))
left,right=fields(old),fields(new)
assert left.keys()==right.keys()
changed={key for key in left if left[key]!=right[key]}
assert changed<={"GIT_SOURCE_DIR","GIT_TEST_GITPERLLIB","GIT_TEST_TEMPLATE_DIR",
                 "GIT_TEST_TEXTDOMAINDIR","GITWEBDIR","LOCALEDIR","PERL_LOCALEDIR"}
assert all(os.path.isabs(left[key]) and os.path.isabs(right[key]) for key in changed)
# Original upstream installed-build mode selects the same already-built Git
# instead of relocated bin-wrappers with embedded stale paths.
env.update(GIT_TEST_INSTALLED=str(work),GIT_EXEC_PATH=str(work))
env["GIT_SKIP_TESTS"]=SKIP
def suite_run(name):
 # Same command the t/Makefile target runs for each suite, eight at a time.
 return name,subprocess.run(["sh",name,"--tee"],cwd=work/"t",env=env,
                            capture_output=True,text=True,timeout=3600)
import concurrent.futures
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
 runs=dict(pool.map(suite_run,suites))
outcomes={}
for suite in suites:
 for line in runs[suite].stdout.splitlines():
  if re.match(r"^(ok|not ok) \d+",line):
   key=suite+"::"+re.sub(r"^(ok|not ok) ","",line)
   assert key not in outcomes
   outcomes[key]="skip" if "# SKIP" in line or "# skip" in line else (
       "passed" if line.startswith("ok ") else "failed")
def digest(rows):return hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(",",":")).encode()).hexdigest()
ledger=digest(outcomes)
original=digest({k:v for k,v in outcomes.items() if k.split("::")[0] in ORIGINAL})
exit_codes={suite:runs[suite].returncode for suite in suites}
passed=(original==ORIGINAL_LEDGER and EXPECTED_LEDGER is not None and
        len(outcomes)==EXPECTED_IDENTITIES and ledger==EXPECTED_LEDGER)
class r:
 returncode=max(exit_codes.values())
 stdout="".join(runs[s].stdout[-2000:] for s in suites if runs[s].returncode)
 stderr="".join(runs[s].stderr[-1000:] for s in suites if runs[s].returncode)
result={"passed":passed,"outcomes":outcomes,"exit_code":r.returncode,
        "suite_exit_codes":exit_codes,"skip":SKIP,
        "ledger_sha256":ledger,"original_ledger_sha256":original,
        "counts":{k:sum(1 for v in outcomes.values() if v==k) for k in ("passed","failed","skip")},
        "scope":"Five original diff suites plus the whitespace, context and merge-file suites; exact pristine identity/outcome ledger (environment failures included); not the entire Git suite.",
        "preparation":{"command":command,"exit_code":prep.returncode,
                       "arm_source":"/app/build/project",
                       "arm_full_tree_inventory":arm_source_inventory,
                       "copied_full_tree_inventory":copied_inventory,
                       "copied_from_build_under_test":True,
                       "full_inventory_excluding_build_options_before":before,
                       "full_inventory_excluding_build_options_after":after,
                       "changed_path_keys":sorted(changed),
                       "old_options_sha256":hashlib.sha256(old.encode()).hexdigest(),
                       "new_options_sha256":hashlib.sha256(new.encode()).hexdigest(),
                       "no_rebuild":True,"test_mode":"original upstream GIT_TEST_INSTALLED, eight suites at a time",
                       "path_changes":{key:{"old":left[key],"new":right[key]} for key in sorted(changed)}}}
if not passed:result["diagnostic_tail"]=(r.stdout+r.stderr)[-6000:]
Path("/scratch/upstream.json").write_text(json.dumps(result,sort_keys=True)+"\n")
print(json.dumps(result,sort_keys=True))
