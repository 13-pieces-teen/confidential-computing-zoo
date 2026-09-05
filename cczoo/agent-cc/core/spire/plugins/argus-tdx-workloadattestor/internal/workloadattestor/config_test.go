package workloadattestor

import (
	"fmt"
	"strings"
	"testing"
)

func validConfig(t *testing.T) string {
	d := fixture(t)
	return fmt.Sprintf(`
 evidence_endpoint="unix:///run/argus/evidence-provider.sock"
 trustee_endpoint="https://trustee.example"
 target_registration_path="/run/argus-workload/target.json"
 trustee_ca_path="/etc/argus/ca.pem"
 trustee_server_name="trustee.example"
 ear_public_key_path="/etc/argus/ear.pem"
 ear_expected_issuer="https://trustee.example"
 ear_expected_profile="tag:github.com,2024:confidential-containers/Trustee"
 workload_id=%q
 policy_id=%q
 image_config_digest=%q
 config_digest=%q
 `, d.WorkloadID, d.PolicyID, d.ImageConfigDigest, d.ConfigDigest)
}
func TestConfigRequiresFixedTrustAndLocalEvidence(t *testing.T) {
	valid := validConfig(t)
	if _, notes := parseConfig(valid); len(notes) > 0 {
		t.Fatal(notes)
	}
	for _, pair := range [][2]string{
		{"unix:///run/argus/evidence-provider.sock", "http://127.0.0.1/evidence"},
		{"https://trustee.example", "http://trustee.example"},
		{"https://trustee.example", "https://trustee.example/custom"},
		{fixture(t).ImageConfigDigest, "openviking:latest"},
		{"ear_public_key_path", "retired_field"},
	} {
		if _, notes := parseConfig(strings.ReplaceAll(valid, pair[0], pair[1])); len(notes) == 0 {
			t.Errorf("accepted %v", pair)
		}
	}
}
