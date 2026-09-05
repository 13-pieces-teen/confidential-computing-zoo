package trustee

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/sha512"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-workloadattestor/internal/protocol"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

const (
	testPolicyID = "argus-workload-openviking-v1"
	testIssuer   = "https://trustee.argus.local"
	testProfile  = "tag:github.com,2024:confidential-containers/Trustee"
)

func fixture(t *testing.T) protocol.Evidence {
	t.Helper()
	b, err := os.ReadFile("../../../../workload/testdata/runtime-data.json")
	if err != nil {
		t.Fatal(err)
	}
	var v struct {
		RuntimeData protocol.RuntimeData `json:"runtime_data"`
	}
	if err = json.Unmarshal(b, &v); err != nil {
		t.Fatal(err)
	}
	return protocol.Evidence{EvidenceType: "tdx_quote", Quote: "AQIDBA", RuntimeData: v.RuntimeData}
}
func TestStructuredTrusteeRequest(t *testing.T) {
	ev := fixture(t)
	canonical, _ := ev.RuntimeData.Canonical()
	now := time.Now()
	key := newSigningKey(t)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" || r.URL.Path != "/attestation" {
			t.Errorf("unexpected endpoint %s", r.URL)
		}
		var request attestationRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Error(err)
			return
		}
		if len(request.VerificationRequests) != 1 || len(request.PolicyIDs) != 1 || request.PolicyIDs[0] != testPolicyID {
			t.Errorf("invalid request %#v", request)
			return
		}
		v := request.VerificationRequests[0]
		if v.RuntimeData.Structured != ev.RuntimeData || v.RuntimeDataHashAlgorithm != "sha384" || v.TEE != "tdx" {
			t.Error("structured binding differs")
		}
		inner, _ := base64.RawURLEncoding.DecodeString(v.Evidence)
		var quote tdxEvidence
		if err := json.Unmarshal(inner, &quote); err != nil || quote.Quote != "AQIDBA==" || quote.CCEventLog != nil {
			t.Errorf("invalid inner evidence %s", inner)
		}
		_, _ = w.Write([]byte(signEAR(t, key, validClaims(now, canonical))))
	}))
	defer server.Close()
	if err := testClient(server, key, now).Verify(context.Background(), ev); err != nil {
		t.Fatal(err)
	}
}
func TestVerifyWorkloadRejectsInvalidEAR(t *testing.T) {
	now := time.Date(2026, 8, 27, 1, 2, 3, 0, time.UTC)
	ev := fixture(t)
	runtimeData, _ := ev.RuntimeData.Canonical()
	key := newSigningKey(t)
	otherKey := newSigningKey(t)

	tests := map[string]func(map[string]any) (*ecdsa.PrivateKey, map[string]any){
		"signature": func(claims map[string]any) (*ecdsa.PrivateKey, map[string]any) { return otherKey, claims },
		"issuer": func(claims map[string]any) (*ecdsa.PrivateKey, map[string]any) {
			claims["iss"] = "https://other.example"
			return key, claims
		},
		"profile": func(claims map[string]any) (*ecdsa.PrivateKey, map[string]any) {
			claims["eat_profile"] = "other-profile"
			return key, claims
		},
		"not yet valid": func(claims map[string]any) (*ecdsa.PrivateKey, map[string]any) {
			claims["nbf"] = now.Add(time.Minute).Unix()
			return key, claims
		},
		"expired": func(claims map[string]any) (*ecdsa.PrivateKey, map[string]any) {
			claims["exp"] = now.Unix()
			return key, claims
		},
		"missing issued at": func(claims map[string]any) (*ecdsa.PrivateKey, map[string]any) {
			delete(claims, "iat")
			return key, claims
		},
		"status": func(claims map[string]any) (*ecdsa.PrivateKey, map[string]any) {
			cpu := claims["submods"].(map[string]any)["cpu0"].(map[string]any)
			cpu["ear.status"] = "contraindicated"
			return key, claims
		},
		"policy": func(claims map[string]any) (*ecdsa.PrivateKey, map[string]any) {
			cpu := claims["submods"].(map[string]any)["cpu0"].(map[string]any)
			cpu["ear.appraisal-policy-id"] = "other-policy"
			return key, claims
		},
		"report data": func(claims map[string]any) (*ecdsa.PrivateKey, map[string]any) {
			cpu := claims["submods"].(map[string]any)["cpu0"].(map[string]any)
			annotated := cpu["ear.veraison.annotated-evidence"].(map[string]any)
			annotated["report_data"] = strings.Repeat("00", 64)
			return key, claims
		},
	}

	for name, mutate := range tests {
		t.Run(name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
				signingKey, claims := mutate(validClaims(now, runtimeData))
				_, _ = writer.Write([]byte(signEAR(t, signingKey, claims)))
			}))
			defer server.Close()

			client := testClient(server, key, now)
			if err := client.Verify(context.Background(), ev); err == nil {
				t.Fatalf("invalid %s EAR was accepted", name)
			}
		})
	}
}

func TestVerifyWorkloadDoesNotRetryTrusteeFailure(t *testing.T) {
	key := newSigningKey(t)
	postCount := 0
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		postCount++
		writer.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer server.Close()

	client := testClient(server, key, time.Now())
	if err := client.Verify(context.Background(), fixture(t)); err == nil {
		t.Fatal("Trustee failure was accepted")
	}
	if postCount != 1 {
		t.Fatalf("POST count = %d, want 1", postCount)
	}
}

func testClient(server *httptest.Server, key *ecdsa.PrivateKey, now time.Time) *Client {
	return &Client{
		httpClient:       server.Client(),
		attestationURL:   server.URL + "/attestation",
		earPublicKey:     &key.PublicKey,
		expectedIssuer:   testIssuer,
		expectedProfile:  testProfile,
		policyID:         testPolicyID,
		maxResponseBytes: 1 << 20,
		now:              func() time.Time { return now },
	}
}

func validClaims(now time.Time, runtimeData []byte) map[string]any {
	reportDigest := sha512.Sum384(runtimeData)
	reportData := append(reportDigest[:], make([]byte, 16)...)
	return map[string]any{
		"eat_profile": testProfile,
		"iss":         testIssuer,
		"iat":         now.Add(-time.Second).Unix(),
		"exp":         now.Add(time.Minute).Unix(),
		"submods": map[string]any{
			"cpu0": map[string]any{
				"ear.status":              "affirming",
				"ear.appraisal-policy-id": testPolicyID,
				"ear.veraison.annotated-evidence": map[string]any{
					"report_data": hex.EncodeToString(reportData),
				},
			},
		},
	}
}

func newSigningKey(t *testing.T) *ecdsa.PrivateKey {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	return key
}

func signEAR(t *testing.T, key *ecdsa.PrivateKey, claims map[string]any) string {
	t.Helper()
	header, err := json.Marshal(map[string]any{"alg": "ES256", "typ": "JWT"})
	if err != nil {
		t.Fatal(err)
	}
	payload, err := json.Marshal(claims)
	if err != nil {
		t.Fatal(err)
	}
	encodedHeader := base64.RawURLEncoding.EncodeToString(header)
	encodedPayload := base64.RawURLEncoding.EncodeToString(payload)
	signingInput := encodedHeader + "." + encodedPayload
	digest := sha256.Sum256([]byte(signingInput))
	r, s, err := ecdsa.Sign(rand.Reader, key, digest[:])
	if err != nil {
		t.Fatal(err)
	}
	signature := make([]byte, 64)
	r.FillBytes(signature[:32])
	s.FillBytes(signature[32:])
	return fmt.Sprintf("%s.%s", signingInput, base64.RawURLEncoding.EncodeToString(signature))
}
func TestRejectsLegacyAllowJSON(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte(`{"allow":true}`)) }))
	defer server.Close()
	if err := testClient(server, newSigningKey(t), time.Now()).Verify(context.Background(), fixture(t)); err == nil {
		t.Fatal("accepted unsigned allow response")
	}
}
