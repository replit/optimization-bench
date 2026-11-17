"""Small native transport/contract smoke, explicitly not a qualification campaign."""
import gzip
import importlib.util
import json
import os
from pathlib import Path
import subprocess

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("goldmark_adapter",HERE/"adapter.py")
adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
root=Path(os.environ.get("GOLDMARK_SMOKE_OUT","/root/fuzz-work/smoke"))
root.mkdir(parents=True,exist_ok=False)
image=os.environ.get("GOLDMARK_FUZZ_IMAGE","goldmark-review:pristine")
cases=[]
for mode in adapter.MODES:
    for fault in ([],[{"kind":"fail_after","limit":17}],[{"kind":"eintr_after","limit":0}]):
        case={"task":adapter.TASK,"surface_id":adapter.SURFACE,
              "contract_id":"goldmark.public.parse-render-exact",
              "precondition_id":"goldmark.valid-markdown-and-constructed-options",
              "input":{"markdown_utf8":"# Header\n\nAn @example [link][late].\n\n[late]: /target\n\nnote[^n]\n\n[^n]: details\n"},
              "options":{"mode":mode},
              "sequence":["init","parse","render","reuse","parse","render","finish"],
              "faults":fault}
        case["case_sha256"]=adapter.digest(case)
        assert adapter.precondition(case,{})["valid"]
        cases.append(case)
results=[];observations={}
for index,case in enumerate(cases):
    arms={}
    for arm in ("pristine","opus"):
        command=["docker","run","--rm","-i","--network","none","--cpuset-cpus","8-15",
                 "--memory","16g","-e","GOMAXPROCS=8","-v","/root/fuzz-work:/build",
                 image,"/build/"+arm+"/worker"]
        child=subprocess.run(command,input=json.dumps(case).encode(),capture_output=True,timeout=60)
        (root/f"{index}-{arm}.stderr").write_bytes(child.stderr)
        assert child.returncode==0,(index,arm,child.returncode)
        arms[arm]=json.loads(child.stdout)
        assert arms[arm]["identity"]["case_sha256"]==case["case_sha256"]
        with gzip.open(root/f"{index}-{arm}.json.gz","wt")as f:json.dump(arms[arm],f)
    result=adapter.compare(arms["pristine"],arms["opus"],case)
    results.append({"index":index,"case_sha256":case["case_sha256"],**result})
    if index==0:
        (root/"sample-case.json").write_text(json.dumps(case,indent=2)+"\n")
        (root/"sample-observation.json").write_text(json.dumps(arms["pristine"],indent=2)+"\n")
(root/"cases.json").write_text(json.dumps(cases,indent=2)+"\n")
(root/"summary.json").write_text(json.dumps({"status":"smoke-only, not qualification",
    "image":image,"actual_task_overlay_used":False,"cases":len(cases),
    "results":results,"coverage_thresholds":"not assessed","mutants":"not assessed",
    "memory":"report-only, not collected"},indent=2)+"\n")
assert all(r["equal"]for r in results)
print("Native public-worker smoke:",len(results),"both-arm matches; not qualification")
