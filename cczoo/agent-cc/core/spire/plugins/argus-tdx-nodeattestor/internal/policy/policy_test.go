package policy

import (
	"bytes"
	"strings"
	"testing"
)

const validPolicy = `
version: 1
policy_id: openviking-prod-v1
tee:
  type: tdx
  allow_debug: false
  allowed_tcb_status: [up_to_date, up_to_date]
  allowed_mrtd: [aabb, 0011]
  allowed_rtmr:
    "1": [ccdd]
    "0": [2233, 0011, 0011]
binding:
  require_report_data: true
  require_attestation_key_digest: true
  require_instance_id: true
`

func TestParseNormalizesAndHashesModel(t *testing.T) {
	first, err := Parse([]byte(validPolicy))
	if err != nil {
		t.Fatal(err)
	}
	second, err := Parse([]byte(strings.ReplaceAll(validPolicy, "allowed_mrtd: [aabb, 0011]", "allowed_mrtd: [0011, aabb]")))
	if err != nil {
		t.Fatal(err)
	}
	if first.Digest != second.Digest || !bytes.Equal(first.CanonicalJSON, second.CanonicalJSON) {
		t.Fatal("semantically equivalent policies produced different canonical models")
	}
	if got := first.Model.TEE.AllowedMRTD; len(got) != 2 || got[0] != "0011" || got[1] != "aabb" {
		t.Fatalf("normalized MRTD = %v", got)
	}
	if !first.AllowsTCBStatus("up_to_date") || !first.AllowsMRTD("aabb") || !first.AllowsRTMR("0", "2233") {
		t.Fatal("normalized allowlists did not match expected values")
	}
}

func TestParseRejectsUnknownFieldAndEmptyAllowlist(t *testing.T) {
	unknown := validPolicy + "unexpected: true\n"
	if _, err := Parse([]byte(unknown)); err == nil {
		t.Fatal("unknown policy field was accepted")
	}
	empty := strings.ReplaceAll(validPolicy, "allowed_tcb_status: [up_to_date, up_to_date]", "allowed_tcb_status: []")
	if _, err := Parse([]byte(empty)); err == nil {
		t.Fatal("empty TCB allowlist was accepted")
	}
}

func TestParseRejectsUnsafeBindingAndMeasurements(t *testing.T) {
	unsafeBinding := strings.ReplaceAll(validPolicy, "require_report_data: true", "require_report_data: false")
	if _, err := Parse([]byte(unsafeBinding)); err == nil {
		t.Fatal("disabled report_data binding was accepted")
	}
	uppercase := strings.ReplaceAll(validPolicy, "allowed_mrtd: [aabb, 0011]", "allowed_mrtd: [AABB]")
	if _, err := Parse([]byte(uppercase)); err == nil {
		t.Fatal("uppercase measurement was accepted")
	}
}
