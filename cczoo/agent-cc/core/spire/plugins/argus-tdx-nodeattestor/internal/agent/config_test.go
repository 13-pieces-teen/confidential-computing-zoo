package agent

import (
	"context"
	"testing"

	configv1 "github.com/spiffe/spire-plugin-sdk/proto/spire/service/common/config/v1"
)

func TestParseConfigAcceptsUnixAndLoopbackHTTP(t *testing.T) {
	core := &configv1.CoreConfiguration{TrustDomain: "argus.local"}
	for _, input := range []string{
		`evidence_endpoint = "unix:///run/argus/evidence.sock"
attestation_key_path = "/var/lib/spire/argus-tdx/attestation-key"`,
		`evidence_endpoint = "http://127.0.0.1:8008/ra/v1/evidence"
attestation_key_path = "/var/lib/spire/argus-tdx/attestation-key"`,
	} {
		if _, notes := parseConfig(core, input); len(notes) != 0 {
			t.Fatalf("valid config notes = %v", notes)
		}
	}
}

func TestParseConfigRejectsRemoteHTTPAndRelativeKey(t *testing.T) {
	config, notes := parseConfig(&configv1.CoreConfiguration{TrustDomain: "argus.local"}, `
evidence_endpoint = "http://192.0.2.10:8008/ra/v1/evidence"
attestation_key_path = "attestation-key"
`)
	if config != nil || len(notes) != 2 {
		t.Fatalf("config = %#v, notes = %v", config, notes)
	}
}

func TestValidateDoesNotChangeConfiguredSnapshot(t *testing.T) {
	plugin := New()
	valid := `evidence_endpoint = "unix:///run/argus/evidence.sock"
attestation_key_path = "/var/lib/spire/argus-tdx/attestation-key"`
	if _, err := plugin.Configure(context.Background(), &configv1.ConfigureRequest{
		CoreConfiguration: &configv1.CoreConfiguration{TrustDomain: "argus.local"},
		HclConfiguration:  valid,
	}); err != nil {
		t.Fatal(err)
	}
	before, err := plugin.getConfig()
	if err != nil {
		t.Fatal(err)
	}
	response, err := plugin.Validate(context.Background(), &configv1.ValidateRequest{
		CoreConfiguration: &configv1.CoreConfiguration{TrustDomain: "argus.local"},
		HclConfiguration:  `evidence_endpoint = "http://remote.example/"`,
	})
	if err != nil {
		t.Fatal(err)
	}
	if response.Valid {
		t.Fatal("invalid config was reported valid")
	}
	after, err := plugin.getConfig()
	if err != nil {
		t.Fatal(err)
	}
	if before != after {
		t.Fatal("Validate changed the active config snapshot")
	}
}
