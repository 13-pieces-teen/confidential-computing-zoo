package workloadattestor

import "testing"

func TestParseConfigAcceptsMockBoundaryEndpoints(t *testing.T) {
	config, notes := parseConfig(`
evidence_endpoint = "http://127.0.0.1:18080/ra/v1/workload-evidence"
trustee_endpoint = "https://mock-trustee:18443/v1/verify/tdx-workload"
trustee_ca_path = "/opt/spire/conf/certs/ca.pem"
trustee_client_cert_path = "/opt/spire/conf/certs/client.pem"
trustee_client_key_path = "/opt/spire/conf/certs/client-key.pem"
trustee_server_name = "trustee.argus.local"
trustee_spiffe_id = "spiffe://argus.local/trustee"
request_timeout = "10s"
max_response_bytes = 1048576
`)
	if len(notes) != 0 {
		t.Fatalf("notes = %v", notes)
	}
	if config.EvidenceEndpoint.Path != "/ra/v1/workload-evidence" {
		t.Fatalf("evidence endpoint = %s", config.EvidenceEndpoint)
	}
	if config.TrusteeSPIFFEID != "spiffe://argus.local/trustee" {
		t.Fatalf("Trustee SPIFFE ID = %q", config.TrusteeSPIFFEID)
	}
}

func TestParseConfigRejectsExpandedBoundary(t *testing.T) {
	_, notes := parseConfig(`
evidence_endpoint = "http://192.0.2.10:18080/ra/v1/workload-evidence"
trustee_endpoint = "http://mock-trustee:18443/v1/verify/tdx-workload"
trustee_ca_path = "relative/ca.pem"
trustee_client_cert_path = "relative/client.pem"
trustee_client_key_path = "relative/client-key.pem"
trustee_server_name = ""
trustee_spiffe_id = "not-a-spiffe-id"
`)
	if len(notes) != 7 {
		t.Fatalf("notes = %v, want seven validation failures", notes)
	}
}
