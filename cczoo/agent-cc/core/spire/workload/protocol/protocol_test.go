package protocol

import (
	"encoding/json"
	"os"
	"testing"
)

func TestSharedRustTrusteeVector(t *testing.T) {
	b, err := os.ReadFile("../testdata/runtime-data.json")
	if err != nil {
		t.Fatal(err)
	}
	var v struct {
		RuntimeData RuntimeData `json:"runtime_data"`
		Canonical   string      `json:"canonical"`
		ReportData  string      `json:"report_data_hex"`
	}
	if err = json.Unmarshal(b, &v); err != nil {
		t.Fatal(err)
	}
	canonical, err := v.RuntimeData.Canonical()
	if err != nil || string(canonical) != v.Canonical {
		t.Fatalf("canonical: %v", err)
	}
	digest, err := ReportDataHex(v.RuntimeData)
	if err != nil || digest != v.ReportData {
		t.Fatalf("report data %s %v", digest, err)
	}
}
func TestStrictSchema(t *testing.T) {
	for _, input := range []string{`{"unknown":"x"}`, `{}{}`} {
		var r EvidenceRequest
		if Decode([]byte(input), &r) == nil {
			t.Fatal("accepted", input)
		}
	}
	for _, nonce := range []string{"", "AA==", "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="} {
		if ValidateNonce(nonce) == nil {
			t.Fatal("accepted nonce", nonce)
		}
	}
}
