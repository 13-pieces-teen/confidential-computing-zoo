import unittest

from spiffe_server.server import certificate_uri_sans, is_exact_spiffe_identity


class ExactSPIFFEIdentityTests(unittest.TestCase):
    def test_accepts_one_exact_identity(self):
        self.assertTrue(
            is_exact_spiffe_identity(
                ["spiffe://argus.local/agent/openclaw"],
                "spiffe://argus.local/agent/openclaw",
            )
        )

    def test_rejects_missing_wrong_or_multiple_identities(self):
        expected = "spiffe://argus.local/agent/openclaw"
        self.assertFalse(is_exact_spiffe_identity([], expected))
        self.assertFalse(is_exact_spiffe_identity(["spiffe://argus.local/agent/other"], expected))
        self.assertFalse(is_exact_spiffe_identity([expected, "spiffe://argus.local/agent/other"], expected))

    def test_extracts_only_uri_subject_alternative_names(self):
        self.assertEqual(
            certificate_uri_sans(
                {
                    "subjectAltName": (
                        ("DNS", "openviking.argus.local"),
                        ("URI", "spiffe://argus.local/agent/openclaw"),
                    )
                }
            ),
            ["spiffe://argus.local/agent/openclaw"],
        )
        self.assertEqual(certificate_uri_sans(None), [])


if __name__ == "__main__":
    unittest.main()
