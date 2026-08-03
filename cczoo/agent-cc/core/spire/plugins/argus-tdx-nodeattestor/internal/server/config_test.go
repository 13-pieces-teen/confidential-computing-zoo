package server

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	configv1 "github.com/spiffe/spire-plugin-sdk/proto/spire/service/common/config/v1"
)

func TestParseConfigLoadsImmutablePolicyAndTLS(t *testing.T) {
	directory := t.TempDir()
	paths := writeConfigFixtures(t, directory)
	config, notes := parseConfig(&configv1.CoreConfiguration{TrustDomain: "argus.local"}, validServerHCL(paths))
	if len(notes) != 0 {
		t.Fatalf("config notes = %v", notes)
	}
	if config.Policy.Model.PolicyID != "openviking-prod-v1" || config.Policy.Digest == "" {
		t.Fatalf("loaded policy = %#v", config.Policy)
	}
	if config.TrusteeTLSConfig.ServerName != "trustee.argus.local" || len(config.TrusteeTLSConfig.Certificates) != 1 {
		t.Fatal("Trustee TLS configuration was not loaded")
	}
	if config.TrusteeURL.Path != verifyPath {
		t.Fatalf("Trustee path = %q", config.TrusteeURL.Path)
	}
}

func TestParseConfigRejectsUnsafeInputs(t *testing.T) {
	directory := t.TempDir()
	paths := writeConfigFixtures(t, directory)
	if err := os.Chmod(paths.key, 0o644); err != nil {
		t.Fatal(err)
	}
	input := strings.ReplaceAll(validServerHCL(paths), `trustee_url = "https://trustee.argus.local"`, `trustee_url = "http://trustee.argus.local"`)
	config, notes := parseConfig(&configv1.CoreConfiguration{TrustDomain: "argus.local"}, input)
	if config != nil || len(notes) < 2 {
		t.Fatalf("config = %#v, notes = %v", config, notes)
	}
}

type fixturePaths struct {
	ca, cert, key, policy string
}

func writeConfigFixtures(t *testing.T, directory string) fixturePaths {
	t.Helper()
	_, caPrivate, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	caTemplate := &x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: "Test CA"},
		NotBefore:             now.Add(-time.Hour),
		NotAfter:              now.Add(time.Hour),
		IsCA:                  true,
		BasicConstraintsValid: true,
		KeyUsage:              x509.KeyUsageCertSign,
	}
	caDER, err := x509.CreateCertificate(rand.Reader, caTemplate, caTemplate, caPrivate.Public(), caPrivate)
	if err != nil {
		t.Fatal(err)
	}
	_, clientPrivate, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	clientTemplate := &x509.Certificate{
		SerialNumber: big.NewInt(2),
		Subject:      pkix.Name{CommonName: "SPIRE Server"},
		NotBefore:    now.Add(-time.Hour),
		NotAfter:     now.Add(time.Hour),
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth},
		KeyUsage:     x509.KeyUsageDigitalSignature,
	}
	clientDER, err := x509.CreateCertificate(rand.Reader, clientTemplate, caTemplate, clientPrivate.Public(), caPrivate)
	if err != nil {
		t.Fatal(err)
	}
	paths := fixturePaths{
		ca:     filepath.Join(directory, "ca.pem"),
		cert:   filepath.Join(directory, "client.pem"),
		key:    filepath.Join(directory, "client-key.pem"),
		policy: filepath.Join(directory, "policy.yaml"),
	}
	writePEM(t, paths.ca, "CERTIFICATE", caDER, 0o644)
	writePEM(t, paths.cert, "CERTIFICATE", clientDER, 0o644)
	privateDER, err := x509.MarshalPKCS8PrivateKey(clientPrivate)
	if err != nil {
		t.Fatal(err)
	}
	writePEM(t, paths.key, "PRIVATE KEY", privateDER, 0o600)
	if err := os.WriteFile(paths.policy, []byte(validPolicyYAML), 0o644); err != nil {
		t.Fatal(err)
	}
	return paths
}

func writePEM(t *testing.T, path, kind string, contents []byte, mode os.FileMode) {
	t.Helper()
	if err := os.WriteFile(path, pem.EncodeToMemory(&pem.Block{Type: kind, Bytes: contents}), mode); err != nil {
		t.Fatal(err)
	}
}

func validServerHCL(paths fixturePaths) string {
	return `
trustee_url = "https://trustee.argus.local"
trustee_ca_path = "` + paths.ca + `"
trustee_client_cert_path = "` + paths.cert + `"
trustee_client_key_path = "` + paths.key + `"
trustee_server_name = "trustee.argus.local"
trustee_expected_spiffe_id = "spiffe://argus.local/service/trustee"
trustee_auth_mode = "mtls_files"
policy_path = "` + paths.policy + `"
challenge_ttl = "30s"
verifier_timeout = "15s"
max_evidence_bytes = 4194304
`
}

const validPolicyYAML = `
version: 1
policy_id: openviking-prod-v1
tee:
  type: tdx
  allow_debug: false
  allowed_tcb_status: [up_to_date]
  allowed_mrtd: [aabb]
  allowed_rtmr:
    "0": [0011]
binding:
  require_report_data: true
  require_attestation_key_digest: true
  require_instance_id: true
`
