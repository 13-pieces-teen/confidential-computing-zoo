package protocol

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
)

func TestCanonicalEvidenceRequestRejectsDuplicateAndUnknownFields(t *testing.T) {
	vector := loadGoldenVector(t)

	duplicate := []byte(`{"version":"v1","version":"v1"}`)
	if _, _, err := CanonicalEvidenceRequest(duplicate); err == nil || !strings.Contains(err.Error(), "duplicate") {
		t.Fatalf("duplicate key error = %v", err)
	}

	var request map[string]any
	if err := json.Unmarshal(vector.EvidenceRequest, &request); err != nil {
		t.Fatal(err)
	}
	request["unexpected"] = true
	unknown, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := CanonicalEvidenceRequest(unknown); err == nil {
		t.Fatal("unknown EvidenceRequest field was accepted")
	}
}

func TestCanonicalBindingClaimsRejectsSchemaDriftAndFloats(t *testing.T) {
	vector := loadGoldenVector(t)
	var claims map[string]any
	if err := json.Unmarshal(vector.BindingClaims, &claims); err != nil {
		t.Fatal(err)
	}

	identity := claims["service_identity"].(map[string]any)
	identity["launch_id"] = "not-in-v1-wire-schema"
	withLaunchID, err := json.Marshal(claims)
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := CanonicalBindingClaims(withLaunchID); err == nil {
		t.Fatal("document-external launch_id field was accepted")
	}

	delete(identity, "launch_id")
	runtime := claims["runtime_binding"].(map[string]any)
	runtime["owning_pid"] = 1.5
	withFloat, err := json.Marshal(claims)
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := CanonicalBindingClaims(withFloat); err == nil || !strings.Contains(err.Error(), "floating-point") {
		t.Fatalf("floating-point owning_pid error = %v", err)
	}
}

func TestCanonicalBindingClaimsNormalizesSupportSources(t *testing.T) {
	vector := loadGoldenVector(t)
	canonical, claims, err := CanonicalBindingClaims(vector.BindingClaims)
	if err != nil {
		t.Fatal(err)
	}
	if got := claims.ClaimSupport["instance_id"]; !equalStrings(got, []string{"quote", "runtime"}) {
		t.Fatalf("normalized sources = %v", got)
	}
	if !bytes.Contains(canonical, []byte(`"instance_id":["quote","runtime"]`)) {
		t.Fatalf("canonical claims did not contain normalized sources: %s", canonical)
	}
}

func equalStrings(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	for index := range left {
		if left[index] != right[index] {
			return false
		}
	}
	return true
}

func TestCanonicalBindingClaimsRejectsInvalidSupportSource(t *testing.T) {
	vector := loadGoldenVector(t)
	var claims map[string]any
	if err := json.Unmarshal(vector.BindingClaims, &claims); err != nil {
		t.Fatal(err)
	}
	claims["claim_support"].(map[string]any)["instance_id"] = []any{"quote", "line\nbreak"}
	contents, err := json.Marshal(claims)
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := CanonicalBindingClaims(contents); err == nil {
		t.Fatal("support source containing a control character was accepted")
	}
}
