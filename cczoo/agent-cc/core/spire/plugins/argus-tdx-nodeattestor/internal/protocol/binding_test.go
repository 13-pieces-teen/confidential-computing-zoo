package protocol

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"os"
	"testing"
)

type goldenMutation struct {
	Value      string `json:"value"`
	ReportData string `json:"report_data"`
}

type goldenVector struct {
	ProtocolVersion          uint32                    `json:"protocol_version"`
	EvidenceRequest          json.RawMessage           `json:"evidence_request"`
	BindingClaims            json.RawMessage           `json:"binding_claims"`
	CanonicalEvidenceRequest string                    `json:"canonical_evidence_request"`
	CanonicalBindingClaims   string                    `json:"canonical_binding_claims"`
	EvidenceRequestDigest    string                    `json:"evidence_request_digest"`
	BindingDigestSHA384      string                    `json:"binding_digest_sha384"`
	ReportData               string                    `json:"report_data"`
	Mutations                map[string]goldenMutation `json:"mutations"`
}

func TestReportDataGoldenVector(t *testing.T) {
	vector := loadGoldenVector(t)
	if vector.ProtocolVersion != Version {
		t.Fatalf("vector protocol version = %d, want %d", vector.ProtocolVersion, Version)
	}

	canonicalRequest, _, err := CanonicalEvidenceRequest(vector.EvidenceRequest)
	if err != nil {
		t.Fatal(err)
	}
	if string(canonicalRequest) != vector.CanonicalEvidenceRequest {
		t.Fatalf("canonical EvidenceRequest mismatch\ngot:  %s\nwant: %s", canonicalRequest, vector.CanonicalEvidenceRequest)
	}
	canonicalClaims, _, err := CanonicalBindingClaims(vector.BindingClaims)
	if err != nil {
		t.Fatal(err)
	}
	if string(canonicalClaims) != vector.CanonicalBindingClaims {
		t.Fatalf("canonical BindingClaims mismatch\ngot:  %s\nwant: %s", canonicalClaims, vector.CanonicalBindingClaims)
	}

	requestDigest, err := EvidenceRequestDigest(vector.EvidenceRequest)
	if err != nil {
		t.Fatal(err)
	}
	if requestDigest != vector.EvidenceRequestDigest {
		t.Fatalf("EvidenceRequest digest = %s, want %s", requestDigest, vector.EvidenceRequestDigest)
	}

	reportData, err := BindingReportData(vector.EvidenceRequest, vector.BindingClaims)
	if err != nil {
		t.Fatal(err)
	}
	if got := hex.EncodeToString(reportData[:48]); got != vector.BindingDigestSHA384 {
		t.Fatalf("binding digest = %s, want %s", got, vector.BindingDigestSHA384)
	}
	if got := hex.EncodeToString(reportData[:]); got != vector.ReportData {
		t.Fatalf("REPORTDATA = %s, want %s", got, vector.ReportData)
	}
	if !bytes.Equal(reportData[48:], make([]byte, 16)) {
		t.Fatal("REPORTDATA trailing 16 bytes are not zero")
	}
}

func TestReportDataMutations(t *testing.T) {
	vector := loadGoldenVector(t)
	for name, mutation := range vector.Mutations {
		t.Run(name, func(t *testing.T) {
			var request map[string]any
			if err := json.Unmarshal(vector.EvidenceRequest, &request); err != nil {
				t.Fatal(err)
			}
			switch name {
			case "nonce":
				request["nonce"] = mutation.Value
			case "key":
				request["target"].(map[string]any)["target_uri"] = mutation.Value
			case "policy":
				request["profile_digest"] = mutation.Value
			default:
				t.Fatalf("unknown mutation %q", name)
			}
			mutatedRequest, err := json.Marshal(request)
			if err != nil {
				t.Fatal(err)
			}
			reportData, err := BindingReportData(mutatedRequest, vector.BindingClaims)
			if err != nil {
				t.Fatal(err)
			}
			if got := hex.EncodeToString(reportData[:]); got != mutation.ReportData {
				t.Fatalf("mutated REPORTDATA = %s, want %s", got, mutation.ReportData)
			}
			if mutation.ReportData == vector.ReportData {
				t.Fatal("mutation did not change REPORTDATA")
			}
		})
	}
}

func loadGoldenVector(t *testing.T) goldenVector {
	t.Helper()
	contents, err := os.ReadFile("testdata/report-data-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var vector goldenVector
	decoder := json.NewDecoder(bytes.NewReader(contents))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&vector); err != nil {
		t.Fatal(err)
	}
	return vector
}
