"""Build genuine no-private fixtures for public package-contract unit tests."""
import hashlib
import json
from pathlib import Path
import shutil
from unittest.mock import patch

import stage_payload
import update_catalog


def without_private_payload(task):
    """Do not make unrelated units depend on real, smudged private corpora."""
    task = Path(task)
    private = task / "tests/heldout"
    shutil.rmtree(private)
    private.mkdir()
    manifest = private / "manifest.json"
    manifest.write_text(json.dumps({"splits": {}}, indent=2) + "\n")
    lock = {"schema_version": 1, "status": "unavailable", "archives": [],
            "files": {"heldout/manifest.json": hashlib.sha256(manifest.read_bytes()).hexdigest()}}
    (task / "tests/heldout.lock.json").write_text(json.dumps(lock, indent=2) + "\n")
    payload_path = task / "payload.json"
    payload = json.loads(payload_path.read_text())
    payload["files"] = [row for row in payload["files"] if row["scope"] != "heldout"]
    payload_path.write_text(json.dumps(payload, indent=2) + "\n")
    status = json.loads((task / "qualification-status.json").read_text())
    status["payload_status"] = ("public-payload-missing" if
                               stage_payload.missing(task, "public", allow_fetch=True) else "ready")
    table = update_catalog.START + "\n" + update_catalog.END
    with patch.object(update_catalog, "catalog", return_value=([task], [], {task: status}, table)):
        for path, text in update_catalog.generated().items():
            if path.is_relative_to(task):
                path.write_text(text)
