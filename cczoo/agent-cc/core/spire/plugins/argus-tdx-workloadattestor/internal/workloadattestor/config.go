package workloadattestor

import (
	"fmt"
	"net"
	"net/url"
	"path"
	"path/filepath"
	"strings"
	"time"

	"github.com/hashicorp/hcl"
	"github.com/spiffe/go-spiffe/v2/spiffeid"
)

// Config contains the validated boundaries and limits used by one configured
// plugin instance.
type Config struct {
	EvidenceEndpoint      *url.URL
	TrusteeEndpoint       *url.URL
	TrusteeCAPath         string
	TrusteeClientCertPath string
	TrusteeClientKeyPath  string
	TrusteeServerName     string
	TrusteeSPIFFEID       string
	RequestTimeout        time.Duration
	MaxResponseBytes      int64
}

type hclConfig struct {
	EvidenceEndpoint      string `hcl:"evidence_endpoint"`
	TrusteeEndpoint       string `hcl:"trustee_endpoint"`
	TrusteeCAPath         string `hcl:"trustee_ca_path"`
	TrusteeClientCertPath string `hcl:"trustee_client_cert_path"`
	TrusteeClientKeyPath  string `hcl:"trustee_client_key_path"`
	TrusteeServerName     string `hcl:"trustee_server_name"`
	TrusteeSPIFFEID       string `hcl:"trustee_spiffe_id"`
	RequestTimeout        string `hcl:"request_timeout"`
	MaxResponseBytes      int64  `hcl:"max_response_bytes"`
}

func parseConfig(input string) (*Config, []string) {
	// Validation reports all configuration problems in one SPIRE response. It
	// does not read credential files; Configure performs that runtime check.
	raw := hclConfig{RequestTimeout: "10s", MaxResponseBytes: 1 << 20}
	if err := hcl.Decode(&raw, input); err != nil {
		return nil, []string{fmt.Sprintf("decode HCL configuration: %v", err)}
	}
	var notes []string
	evidenceEndpoint, err := url.Parse(raw.EvidenceEndpoint)
	if err != nil || evidenceEndpoint.Scheme == "" {
		notes = append(notes, "evidence_endpoint must be a valid unix or loopback HTTP URL")
	} else if err := validateEvidenceEndpoint(evidenceEndpoint); err != nil {
		notes = append(notes, err.Error())
	}
	trusteeEndpoint, err := url.Parse(raw.TrusteeEndpoint)
	if err != nil || trusteeEndpoint.Scheme != "https" || trusteeEndpoint.Host == "" {
		notes = append(notes, "trustee_endpoint must be a valid HTTPS URL")
	}
	for label, value := range map[string]string{
		"trustee_ca_path":          raw.TrusteeCAPath,
		"trustee_client_cert_path": raw.TrusteeClientCertPath,
		"trustee_client_key_path":  raw.TrusteeClientKeyPath,
	} {
		if !absoluteForHostOrLinux(value) {
			notes = append(notes, label+" must be absolute")
		}
	}
	if _, err := spiffeid.FromString(raw.TrusteeSPIFFEID); err != nil {
		notes = append(notes, "trustee_spiffe_id must be a valid SPIFFE ID")
	}
	if raw.TrusteeServerName == "" || strings.ContainsAny(raw.TrusteeServerName, "/:@") {
		notes = append(notes, "trustee_server_name must be a DNS name")
	}
	timeout, err := time.ParseDuration(raw.RequestTimeout)
	if err != nil || timeout <= 0 || timeout > 30*time.Second {
		notes = append(notes, "request_timeout must be greater than zero and no more than 30s")
	}
	if raw.MaxResponseBytes <= 0 || raw.MaxResponseBytes > 4<<20 {
		notes = append(notes, "max_response_bytes must be between 1 and 4194304")
	}
	if len(notes) > 0 {
		return nil, notes
	}
	return &Config{
		EvidenceEndpoint:      evidenceEndpoint,
		TrusteeEndpoint:       trusteeEndpoint,
		TrusteeCAPath:         raw.TrusteeCAPath,
		TrusteeClientCertPath: raw.TrusteeClientCertPath,
		TrusteeClientKeyPath:  raw.TrusteeClientKeyPath,
		TrusteeServerName:     raw.TrusteeServerName,
		TrusteeSPIFFEID:       raw.TrusteeSPIFFEID,
		RequestTimeout:        timeout,
		MaxResponseBytes:      raw.MaxResponseBytes,
	}, nil
}

func validateEvidenceEndpoint(endpoint *url.URL) error {
	// The Evidence Provider consumes a local process PID, so keep collection on the
	// agent-local Unix socket or loopback boundary. Remote trust appraisal uses
	// the separately authenticated Trustee channel.
	switch endpoint.Scheme {
	case "unix":
		if endpoint.Host != "" || !path.IsAbs(endpoint.Path) || endpoint.RawQuery != "" || endpoint.Fragment != "" {
			return fmt.Errorf("unix evidence_endpoint must contain only an absolute socket path")
		}
	case "http":
		host := endpoint.Hostname()
		if host != "localhost" {
			address := net.ParseIP(host)
			if address == nil || !address.IsLoopback() {
				return fmt.Errorf("HTTP evidence_endpoint must use a loopback address")
			}
		}
		if endpoint.Path == "" {
			endpoint.Path = "/ra/v1/workload-evidence"
		}
	default:
		return fmt.Errorf("evidence_endpoint scheme must be unix or http")
	}
	return nil
}

func absoluteForHostOrLinux(value string) bool {
	// SPIRE configuration may target Linux even when validation runs on a
	// Windows development host.
	return filepath.IsAbs(value) || strings.HasPrefix(value, "/")
}
