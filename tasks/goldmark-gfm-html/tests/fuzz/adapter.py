"""Preliminary public-domain generator/oracle hooks; not qualification policy."""
import copy
import hashlib
import json

TASK = "goldmark-gfm-html"
SURFACE = "parser.Parse+html.Render"
ALIAS_SURFACE = "ast.NewDocument.Metadata+ast.NewParagraph"
MODES = ("gfm", "commonmark", "footnotes", "custom", "context")
def typed_equal(left, right):
    """Strict recursive JSON/Python categories, matching central typed_equal."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        if {(type(k),k) for k in left}!={(type(k),k) for k in right}:
            return False
        return all(typed_equal(v,right[k]) for k,v in left.items())
    if isinstance(left,(list,tuple)):
        return len(left)==len(right) and all(typed_equal(a,b) for a,b in zip(left,right))
    if isinstance(left,float):
        return left.hex()==right.hex()
    return left==right
TOKENS = (
    "plain text\n\n", "# heading\n\n", "> quote\nlazy continuation\n\n",
    "- one\n\n  continuation\n\n- two\n\n", "[use][later]\n\n[later]: /target\n\n",
    "````md\n\ninside\n~~~\n````\n\n", "|a|b|\n|-|-|\n|x|y|\n\n",
    "note[^n]\n\n[^n]: definition\n\n", "<div>\nhtml\n</div>\n\n",
)

def digest(case):
    value = {k:v for k,v in case.items() if k != "case_sha256"}
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":")).encode()).hexdigest()

def generate(rng, budget):
    count = budget.get("max_cases",64) if isinstance(budget,dict) else int(budget)
    count = min(max(count,0),4096)
    for _ in range(count):
        text = "".join(rng.choice(TOKENS) for _ in range(rng.randrange(1,25)))
        target = rng.choice((0,1,65535,65536,65537,131071,131072,131073,262144))
        if target: text = (text * (target//max(1,len(text))+1))[:target]
        if rng.randrange(4)==0: text=text.replace("\n","\r\n")
        case={"task":TASK,"surface_id":SURFACE,
              "contract_id":"goldmark.public.parse-render-exact",
              "precondition_id":"goldmark.valid-markdown-and-constructed-options",
              "input":{"markdown_utf8":text},"options":{"mode":rng.choice(MODES)},
              "sequence":["init","parse","render","reuse","parse","render","finish"],
              "faults":[]}
        if rng.randrange(4)==0:
            case["faults"]=[{"kind":rng.choice(("fail_after","eintr_after")),
                            "limit":rng.choice((0,1,17,4096,24000))}]
        case["case_sha256"]=digest(case)
        yield case

def precondition(case, surface):
    if case.get("task")==TASK and case.get("surface_id")==ALIAS_SURFACE:
        if "case_sha256" in case and case["case_sha256"]!=digest(case):
            return {"valid":False,"reason":"Case self-commitment mismatch"}
        value=case.get("input",{}).get("metadata_value")
        try:
            if type(value)is not str or len(value.encode("utf-8"))>1024:
                return {"valid":False,"reason":"Unsupported metadata value transport"}
        except UnicodeEncodeError:
            return {"valid":False,"reason":"Metadata value cannot encode surrogate"}
        valid=(type(value)is str and 0<len(value)<=256
               and case.get("options")=={"mode":"ast_metadata"}
               and case.get("sequence")==["factory","getter","mutate","interleave","fresh","finish"]
               and case.get("faults")==[])
        return {"valid":valid,"reason":"Public independent AST construction and legal metadata/tree mutation" if valid else "Unsupported AST aliasing domain"}
    if case.get("task")!=TASK or case.get("surface_id")!=SURFACE:
        return {"valid":False,"reason":"Unknown task/public surface"}
    if "case_sha256" in case and case["case_sha256"]!=digest(case):
        return {"valid":False,"reason":"Case self-commitment mismatch"}
    text=case.get("input",{}).get("markdown_utf8")
    if not isinstance(text,str):
        return {"valid":False,"reason":"Unsupported input representation"}
    try:
        size=len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return {"valid":False,"reason":"UTF-8 string representation cannot encode surrogate"}
    if size>2*1024*1024:
        return {"valid":False,"reason":"Unsupported input representation or bounded size"}
    if case.get("options",{}).get("mode") not in MODES:
        return {"valid":False,"reason":"Unsupported constructor mode"}
    if case.get("sequence")!=["init","parse","render","reuse","parse","render","finish"]:
        return {"valid":False,"reason":"Unsupported state transition schedule"}
    faults=case.get("faults",[])
    if not isinstance(faults,list) or len(faults)>1:
        return {"valid":False,"reason":"Unsupported fault schedule"}
    for fault in faults:
        if (not isinstance(fault,dict) or fault.get("kind") not in ("fail_after","eintr_after")
            or type(fault.get("limit")) is not int or not 0<=fault["limit"]<=2*1024*1024):
            return {"valid":False,"reason":"Unknown or invalid legal writer fault"}
    return {"valid":True,"reason":"Constructor-valid public parser/HTML mode"}

def shrink(case):
    if not precondition(case,{}).get("valid"): return
    if case.get("surface_id")==ALIAS_SURFACE:
        value=case["input"]["metadata_value"]
        if len(value)>1:
            result=copy.deepcopy(case);result["input"]["metadata_value"]=value[:max(1,len(value)//2)]
            result["case_sha256"]=digest(result);yield result
        return
    text=case["input"]["markdown_utf8"]
    for value in (text[:len(text)//2],text[len(text)//2:],""):
        result=copy.deepcopy(case);result["input"]["markdown_utf8"]=value
        result["case_sha256"]=digest(result);yield result

def compare(baseline,candidate,case):
    """Complete contractual fields only; never normalize error wording wholesale."""
    differences=[]
    for label,obs in (("baseline",baseline),("candidate",candidate)):
        if not isinstance(obs,dict) or obs.get("format")!="optimbench-fuzz-observation-v1":
            differences.append({"arm":label,"kind":"malformed_observation"})
        elif not all(k in obs for k in ("identity","termination","outcome","effects","diagnostics")):
            differences.append({"arm":label,"kind":"incomplete_observation"})
        elif (obs["identity"].get("task")!=case.get("task")
              or obs["identity"].get("surface_id")!=case.get("surface_id")
              or obs["identity"].get("case_sha256")!=case.get("case_sha256")
              or not obs["identity"].get("build_sha256")):
            differences.append({"arm":label,"kind":"unbound_observation_identity"})
    if not differences:
        for key in ("termination","outcome","effects"):
            if not typed_equal(baseline[key],candidate[key]):
                differences.append({"field":key,"baseline":baseline[key],"candidate":candidate[key]})
    return {"equal":not differences,"policy_id":"goldmark.public.parse-render-exact",
            "differences":differences}
