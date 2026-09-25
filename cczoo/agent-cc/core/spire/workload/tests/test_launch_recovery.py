"""Durable client recovery against scripted TC API outcomes; no TDX claims."""
import io
import json
import os
from pathlib import Path
import ssl
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import test_runtime as contracts
import launch_state
runtime = contracts.runtime


class LaunchRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.d = runtime.Deployment(contracts.RuntimeContractTests().config())
        self.d.run, self.d.records = self.root / "run", self.root / "records"
        self.d.target = self.d.run / "target.json"
        self.d.install = self.root / "install"
        self.d.etc = self.root / "config"
        self.calls = []
        self.env = patch.dict(os.environ, {"TC_API_IDENTITY_TOKEN": "must-not-be-recorded"})
        self.env.start()
        self.addCleanup(self.env.stop)
        # Filesystem protection is separately exercised on Linux. Fixtures also run on Windows.
        self.files = patch.object(launch_state, "protected_file", side_effect=Path)
        self.files.start()
        self.addCleanup(self.files.stop)

    def result(self):
        return {"launch_id": "launch-1", "status": "success", "user_id": self.d.c["tc_api_user_id"], "instance_ids": [{
            "launch_id": "launch-1", "container_ID": "a" * 64, "workload_id": self.d.workload["id"],
            "attestation_profile": launch_state.PROFILE,
            "runtime_image_config_digest": self.d.c["approved"]["image_config_digest"]}]}

    def request(self, outcomes):
        def request(c, route, data=None):
            self.calls.append((route, data))
            outcome = outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        return request

    def execute(self, outcomes, **options):
        return launch_state.execute(self.d, self.request(outcomes), runtime.write_json, sleep=lambda _: None, **options)

    def saved(self):
        return json.loads((self.d.records / "launch-state.json").read_text())

    def challenge(self):
        detail = {"launch_id": "launch-1", "retry_path": "/api/deploy-launch/commit/launch-1", "retry_method": "POST"}
        return HTTPError("https://configured/api/launch-result/launch-1", 428, "signing", {}, io.BytesIO(json.dumps({"detail": detail}).encode()))

    def test_completed_launch_is_correlated_and_contains_no_credentials(self):
        result = self.execute([{"launch_id": "launch-1"}, self.result()])
        self.assertEqual(self.saved()["stage"], "complete")
        self.assertEqual(result["container_id"], "a" * 64)
        self.assertEqual(len([c for c in self.calls if c[1] is not None]), 1)
        for path in self.d.records.glob("*.json"):
            self.assertNotIn("must-not-be-recorded", path.read_text())

    def test_server_initial_and_launching_states_continue_polling(self):
        self.execute([{"launch_id": "launch-1"}, dict(self.result(), status="initiated"),
                      dict(self.result(), status="launching"), self.result()])
        self.assertEqual(self.saved()["stage"], "complete")
        self.assertEqual(len(self.calls), 4)

    def test_unknown_submission_is_durable_and_never_reposted(self):
        with self.assertRaisesRegex(ValueError, "outcome unknown"):
            self.execute([TimeoutError("contains secret")])
        self.assertEqual(self.saved()["stage"], "submission_unknown")
        with self.assertRaisesRegex(ValueError, "unfinished"):
            self.execute([])
        self.assertEqual(len(self.calls), 1)
        self.execute([self.result()], resume=True, launch_id="launch-1")
        self.assertTrue(all(data is None for _, data in self.calls[1:]))

    def test_timeout_after_id_can_resume_without_creation(self):
        with self.assertRaises(TimeoutError):
            self.execute([{"launch_id": "launch-1"}], monotonic=iter([0, 601]).__next__)
        self.assertEqual(self.saved()["launch_id"], "launch-1")
        result = self.execute([self.result()], resume=True)
        self.assertTrue(result["resumed"])
        self.assertEqual(len([c for c in self.calls if c[1] is not None]), 1)

    def test_transient_get_retries_but_tls_and_authorization_do_not(self):
        self.execute([{"launch_id": "launch-1"}, TimeoutError(), self.result()])
        self.assertEqual(len(self.calls), 3)
        for error in (URLError(ssl.SSLError("bad cert")), HTTPError("https://configured", 403, "denied", {}, None)):
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, "query"):
                self.execute([error], resume=True)

    def test_changed_configuration_or_launch_id_cannot_adopt_pending_operation(self):
        with self.assertRaises(TimeoutError):
            self.execute([{"launch_id": "launch-1"}], monotonic=iter([0, 601]).__next__)
        with self.assertRaisesRegex(ValueError, "ID differs"):
            self.execute([], resume=True, launch_id="another")
        self.d.c["image_id"] = "changed"
        with self.assertRaisesRegex(ValueError, "configuration differs"):
            self.execute([], resume=True)

    def test_result_must_match_launch_workload_profile_and_image(self):
        for key in ("launch_id", "workload_id", "attestation_profile", "runtime_image_config_digest", "container_ID"):
            bad = self.result()
            bad["instance_ids"][0][key] = "wrong"
            with self.subTest(field=key), self.assertRaisesRegex(ValueError, "association"):
                self.execute([bad], resume=True, launch_id="launch-1")
            self.assertFalse((self.d.run / "launch.json").exists())

    def test_other_users_operation_cannot_be_adopted(self):
        with self.assertRaisesRegex(ValueError, "user mismatch"):
            self.execute([dict(self.result(), user_id="another-user")], resume=True, launch_id="launch-1")
        self.assertFalse((self.d.run / "launch.json").exists())

    def test_signing_requires_explicit_resume_and_reconciles_commit_timeout_by_get(self):
        with self.assertRaisesRegex(ValueError, "fresh signing identity"):
            self.execute([{"launch_id": "launch-1"}, self.challenge()])
        self.execute([self.challenge(), TimeoutError(), self.result()], resume=True)
        posts = [route for route, body in self.calls if body is not None]
        self.assertEqual(posts, ["/api/deploy-launch", "/api/deploy-launch/commit/launch-1"])

    def test_repeated_commit_challenge_does_not_repeat_post(self):
        with self.assertRaisesRegex(ValueError, "fresh signing identity"):
            self.execute([self.challenge(), {}, self.challenge()], resume=True, launch_id="launch-1")
        self.assertEqual(len([c for c in self.calls if c[1] is not None]), 1)

    @unittest.skipUnless(sys.platform == "linux", "flock is the production Linux exclusion mechanism")
    def test_concurrent_operation_is_rejected_and_lock_can_be_reused(self):
        with launch_state.operation_lock(self.d.records):
            with self.assertRaisesRegex(ValueError, "another deployment"):
                with launch_state.operation_lock(self.d.records):
                    self.fail("must not enter")
        with launch_state.operation_lock(self.d.records):
            pass


if __name__ == "__main__":
    unittest.main()
