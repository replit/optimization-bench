import json
import unittest

from tools import public_release_ledger as ledger


class ImageEmbeddingRule(unittest.TestCase):
    def test_workload_directory_copy_from_stage_embeds(self):
        self.assertTrue(ledger.image_embeds_inputs(
            "COPY --from=payload-guard /payload/workload/ /app/workload/\n"))

    def test_inputs_files_copy_embeds(self):
        self.assertTrue(ledger.image_embeds_inputs(
            "COPY --from=suite-inputs /payload/workload/inputs/files /app/workload/inputs/files\n"))

    def test_plain_workload_directory_copy_embeds(self):
        self.assertTrue(ledger.image_embeds_inputs("COPY workload /app/workload\n"))

    def test_manifest_and_code_files_do_not_embed(self):
        self.assertFalse(ledger.image_embeds_inputs(
            "COPY workload/*.py workload/*.sh /app/workload/\n"
            "COPY workload/inputs/manifest.json /app/workload/inputs/manifest.json\n"
            "RUN --mount=type=bind,source=workload/inputs/files,target=/app/workload/inputs/files \\\n"
            "  sh build.sh\n"))

    def test_dockerignored_inputs_do_not_embed_from_context(self):
        self.assertFalse(ledger.image_embeds_inputs(
            "COPY workload/ /app/workload/\n", "workload/inputs/**\n"))


class Validation(unittest.TestCase):
    FACTS = {"t": {"image_embeds_workload_inputs": True}}

    def record(self, **overrides):
        record = {"image_policy": "ecr-after-signoff", "reason": "r", "signoffs": [
            {"item": "i", "evidence": "e", "status": "verified"}]}
        record.update(overrides)
        return {"tasks": {"t": record}}

    def test_valid_record_passes(self):
        self.assertEqual(ledger.validate(self.record(), self.FACTS), [])

    def test_missing_and_unknown_tasks_are_reported(self):
        problems = ledger.validate({"tasks": {"other": {}}}, self.FACTS)
        self.assertTrue(any("missing from the ledger" in p for p in problems))
        self.assertTrue(any("not a Task" in p for p in problems))

    def test_embedding_image_needs_a_rights_item_for_ecr(self):
        problems = ledger.validate(self.record(signoffs=[]), self.FACTS)
        self.assertTrue(any("needs at least one" in p for p in problems))

    def test_publishable_now_requires_all_verified(self):
        record = self.record(publishable_now=True, signoffs=[
            {"item": "i", "evidence": "e", "status": "requires-human-judgment"}])
        self.assertTrue(any("publishable_now" in p for p in ledger.validate(record, self.FACTS)))

    def test_unknown_status_rejected(self):
        record = self.record(signoffs=[{"item": "i", "evidence": "e", "status": "signed"}])
        self.assertTrue(any("status must be" in p for p in ledger.validate(record, self.FACTS)))

    def test_approval_only_on_verified_items(self):
        record = self.record(signoffs=[{"item": "i", "evidence": "e", "status": "blocked",
                                        "approved_by": "team"}])
        self.assertTrue(any("approved_by" in p for p in ledger.validate(record, self.FACTS)))

    def test_release_checks_are_validated(self):
        data = self.record()
        data["release_checks"] = [{"item": "i", "evidence": "", "status": "blocked"}]
        self.assertTrue(any("release_checks[0]: evidence" in p for p in ledger.validate(data, self.FACTS)))

    def test_included_task_cannot_be_held(self):
        data = self.record(image_policy="held")
        data["release_scope"] = {"included": ["t", "missing"]}
        problems = ledger.validate(data, self.FACTS)
        self.assertTrue(any("included but its image_policy is held" in p for p in problems))
        self.assertTrue(any("missing is not in the ledger" in p for p in problems))

    def test_readiness_needs_every_item_verified(self):
        data = self.record(signoffs=[{"item": "i", "evidence": "e", "status": "verified"},
                                     {"item": "j", "evidence": "e", "status": "blocked"}])
        self.assertFalse(ledger.readiness(data)["t"]["ready"])
        self.assertTrue(ledger.readiness(self.record())["t"]["ready"])

    def test_omitted_bodies_exist_in_staging_and_are_absent_in_an_export(self):
        import tempfile
        from pathlib import Path
        data = self.record(omit_from_export=["body"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertTrue(any("does not exist" in p for p in ledger.validate(data, self.FACTS, root)))
            (root / "body").write_text("x")
            self.assertEqual(ledger.validate(data, self.FACTS, root), [])
            (root / "EXPORT.json").write_text("{}")
            self.assertTrue(any("present in the export" in p
                                for p in ledger.validate(data, self.FACTS, root)))
            (root / "body").unlink()
            self.assertEqual(ledger.validate(data, self.FACTS, root), [])

    def test_unknown_policy_rejected(self):
        problems = ledger.validate(self.record(image_policy="ship-it"), self.FACTS)
        self.assertTrue(any("image_policy" in p for p in problems))


class RepositoryLedger(unittest.TestCase):
    def test_checked_in_ledger_matches_the_repository(self):
        data = json.loads(ledger.LEDGER.read_text())
        self.assertEqual(ledger.validate(data, ledger.all_facts()), [])


if __name__ == "__main__":
    unittest.main()
