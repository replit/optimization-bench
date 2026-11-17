#!/usr/bin/env python3
"""Fetch complete original histories; freeze selection before any library run.

Bodies remain fetch-only outside Git. Repository families are assigned to one
split by SHA-256, before input size, baseline outputs or performance is known.
No optimizer code is consulted and no timing is performed.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
from urllib.error import HTTPError
from urllib.request import Request, urlopen

INDEX = "8b1b03f5370e310770ba69fe28b9e8e738765047"
UA = "OptimBench reproducible corpus acquisition"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fetch(url, cache):
    destination = cache / sha(url.encode())
    if destination.is_file():
        return destination.read_bytes()
    for attempt in range(4):
        try:
            with urlopen(Request(url, headers={"User-Agent":UA}), timeout=45) as response:
                data = response.read()
            destination.write_bytes(data)
            return data
        except HTTPError as error:
            if error.code in (400,401,403,404,410) or attempt == 3:
                raise
            time.sleep(2**attempt)
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2**attempt)


def repository(value):
    if not value or not value.startswith(("https://","http://")):
        return None
    parsed = urlsplit(value)
    if parsed.username is not None or parsed.password is not None:
        return None
    pieces = [p for p in parsed.path.split("/") if p]
    if len(pieces) < 2:
        return None
    # GitHub's first two components identify a repository, not /tree/subdir.
    if parsed.hostname and parsed.hostname.lower() in ("github.com","www.github.com"):
        pieces = pieces[:2]
    path = "/".join(pieces).removesuffix(".git").lower()
    return (parsed.hostname or "").lower().removeprefix("www.") + "/" + path


def index_path(name):
    name = name.lower()
    if len(name) == 1:
        return "1/" + name
    if len(name) == 2:
        return "2/" + name
    if len(name) == 3:
        return "3/" + name[0] + "/" + name
    return name[:2] + "/" + name[2:4] + "/" + name


def family(value):
    """Conservative forge owner/organization grouping, broader than one repo."""
    resolved = repository(value)
    if not resolved:
        return None
    return "/".join(resolved.split("/")[:2])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--target-bytes", type=int, default=1024**3)
    parser.add_argument("--maximum-pages", type=int, default=2000)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--supplement-queries",nargs="*",default=[])
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    requests = args.cache/"requests"
    bodies = args.cache/"files"
    requests.mkdir(exist_ok=True)
    bodies.mkdir(exist_ok=True)
    selected = {"public":[], "heldout":[]}
    total = {"public":0,"heldout":0}
    groups, hashes, names = {}, set(), set()
    excluded = []
    pages = []
    legacy = {
        "public":("clap","rand","regex","rustls","serde_json","tokio"),
        "heldout":("bevy","diesel","image","nalgebra","opencv","winit"),
    }
    overrides = {}
    legacy_sources = []
    for split, crates in legacy.items():
        for name in crates:
            url = f"https://crates.io/api/v1/crates/{name}"
            raw = fetch(url,requests)
            crate = json.loads(raw)["crate"]
            group = family(crate.get("repository"))
            assert group, "Original family provenance unresolved: "+name
            assert group not in overrides or overrides[group] == split
            overrides[group] = split
            legacy_sources.append({"name":name,"repository":repository(crate["repository"]),
                                   "family":group,"split":split,
                                   "url":url,"sha256":sha(raw),"bytes":len(raw)})
            time.sleep(.15)

    def original(crate):
        group = family(crate.get("repository"))
        if not group:
            return None, {"name":crate["name"],"reason":"No resolvable repository provenance"}
        split = overrides.get(group) or (
            "public" if int(sha(group.encode())[:8],16)%2 == 0 else "heldout")
        path = index_path(crate["name"])
        url = f"https://raw.githubusercontent.com/rust-lang/crates.io-index/{INDEX}/{path}"
        try:
            data = fetch(url, requests)
            records = data.splitlines()
            parsed = [json.loads(line) for line in records]
            if not records or any(row["name"].lower() != crate["name"].lower() for row in parsed):
                raise ValueError("Original history has unexpected crate identity")
            digest = sha(data)
            (bodies/digest).write_bytes(data)
            return {
                "name":crate["name"],"path":crate["name"],"group":group,
                "repository":crate["repository"],"index_repository":"https://github.com/rust-lang/crates.io-index",
                "repository_normalized":repository(crate["repository"]),
                "index_commit":INDEX,"index_path":path,"url":url,
                "bytes":len(data),"records":len(records),"sha256":digest,"split":split,
                "license":"fetch-only; no redistribution",
                "license_basis":"Original index metadata license is not inferred from published crate source licenses. No input bodies distributed in Git/LFS; fetch exact upstream URL.",
                "transformations":"none; complete original crate version history",
            }, None
        except Exception as error:
            return None, {"name":crate["name"],"url":url,
                          "reason":type(error).__name__+": "+str(error)}

    def catalogs():
        # Provider rejects deep pagination but explicitly supports name-letter
        # filters. Alphabetic scopes enumerate real registry inputs, not padding.
        scopes = [("", "")]
        scopes += [("letter:"+letter,"&letter="+letter)
                   for letter in "abcdefghijklmnopqrstuvwxyz0123456789"]
        scopes += [(query,"&q="+query) for query in args.supplement_queries]
        for query,suffix in scopes:
            for page in range(1,201):
                url = f"https://crates.io/api/v1/crates?page={page}&per_page=100&sort=downloads"
                url += suffix
                yield query,page,url
    finished_queries = set()
    for query,page,url in catalogs():
        if query in finished_queries:
            continue
        if len(pages) >= args.maximum_pages:
            break
        cached_catalog = (requests/sha(url.encode())).is_file()
        raw = fetch(url,requests)
        catalog = json.loads(raw)
        pages.append({"url":url,"sha256":sha(raw),"bytes":len(raw),"query":query})
        crates = [c for c in catalog.get("crates",[]) if c["name"].lower() not in names]
        names.update(c["name"].lower() for c in crates)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            # map preserves catalog rank regardless of network completion order.
            for doc, failure in pool.map(original,crates):
                if failure:
                    excluded.append(failure)
                    continue
                split = doc.pop("split")
                if total[split] >= args.target_bytes:
                    continue
                if doc["sha256"] in hashes:
                    excluded.append({"name":doc["name"],"reason":"Exact content already selected"})
                    continue
                hashes.add(doc["sha256"])
                assert doc["group"] not in groups or groups[doc["group"]] == split
                groups[doc["group"]] = split
                selected[split].append(doc)
                total[split] += doc["bytes"]
        report = {
            "format":"serde-realism-corpus-selection-v1","index_commit":INDEX,
            "selection":"crates.io first 200 download-rank pages, then supported alphabetical name-letter scopes due provider deep-pagination cap; conservative forge owner/organization families, preserve legacy split families, remaining family SHA256 parity; first distinct complete histories reaching declared byte target; no output/performance/candidate selection",
            "target_bytes_per_split":args.target_bytes,"bytes":total,
            "files":{s:len(docs) for s,docs in selected.items()},
            "catalog_pages":pages,"selected":selected,"excluded":excluded,
            "legacy_repository_assignments":legacy_sources,
            "supplement_queries":args.supplement_queries,
            "supplement_letter_scopes":"abcdefghijklmnopqrstuvwxyz0123456789",
            "provider_limit":"Page 201 rejected by crates.io; each catalog scope bounded to 200 pages; names/content globally deduplicated across all scopes",
            "license_policy":"Fetch-only metadata, no redistribution or inferred crate-source license grant",
            "timing_performed":False,"library_executions":0,
        }
        (args.cache/"selection.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
        print(json.dumps({"query":query,"page":page,"bytes":total,"files":report["files"],
                          "excluded":len(excluded)}),flush=True)
        if all(size >= args.target_bytes for size in total.values()):
            break
        if not catalog.get("crates") or not catalog.get("meta",{}).get("next_page"):
            finished_queries.add(query)
        if not cached_catalog:
            time.sleep(1)  # Bounded API rate; cached replay makes no API requests.
    assert all(total[s] >= args.target_bytes for s in total), "Corpus below declared byte target"
    a,b = selected["public"],selected["heldout"]
    assert not {d["group"] for d in a} & {d["group"] for d in b}
    assert not {d["sha256"] for d in a} & {d["sha256"] for d in b}
    assert not {d["name"].lower() for d in a} & {d["name"].lower() for d in b}


if __name__ == "__main__":
    main()
