package agent

import (
	"fmt"
	"net"
	"net/url"
	"path/filepath"
	"strings"
	"time"

	"github.com/hashicorp/hcl"
	configv1 "github.com/spiffe/spire-plugin-sdk/proto/spire/service/common/config/v1"
)

const defaultEvidencePath = "/ra/v1/evidence"

type Config struct {
	TrustDomain        string
	EvidenceEndpoint   *url.URL
	AttestationKeyPath string
	EvidenceTimeout    time.Duration
	MaxEvidenceBytes   int64
	InstanceHint       string
}

type hclConfig struct {
	EvidenceEndpoint   string `hcl:"evidence_endpoint"`
	AttestationKeyPath string `hcl:"attestation_key_path"`
	EvidenceTimeout    string `hcl:"evidence_timeout"`
	MaxEvidenceBytes   int64  `hcl:"max_evidence_bytes"`
	InstanceHint       string `hcl:"instance_hint"`
}

func parseConfig(core *configv1.CoreConfiguration, input string) (*Config, []string) {
	raw := hclConfig{
		EvidenceTimeout:  "10s",
		MaxEvidenceBytes: 4 << 20,
	}
	var notes []string
	if err := hcl.Decode(&raw, input); err != nil {
		return nil, []string{fmt.Sprintf("decode HCL configuration: %v", err)}
	}
	if core == nil || core.TrustDomain == "" {
		notes = append(notes, "core trust_domain is required")
	}
	endpoint, err := url.Parse(raw.EvidenceEndpoint)
	if err != nil || endpoint.Scheme == "" {
		notes = append(notes, "evidence_endpoint must be a valid unix or loopback HTTP URL")
	} else if err := validateEndpoint(endpoint); err != nil {
		notes = append(notes, err.Error())
	}
	if !filepath.IsAbs(raw.AttestationKeyPath) {
		notes = append(notes, "attestation_key_path must be absolute")
	}
	timeout, err := time.ParseDuration(raw.EvidenceTimeout)
	if err != nil || timeout <= 0 || timeout > 30*time.Second {
		notes = append(notes, "evidence_timeout must be greater than zero and no more than 30s")
	}
	if raw.MaxEvidenceBytes <= 0 || raw.MaxEvidenceBytes > 4<<20 {
		notes = append(notes, "max_evidence_bytes must be between 1 and 4194304")
	}
	if len(raw.InstanceHint) > 128 || strings.ContainsAny(raw.InstanceHint, "\r\n\t") {
		notes = append(notes, "instance_hint must be at most 128 printable ASCII bytes")
	}
	for _, character := range []byte(raw.InstanceHint) {
		if character < 0x20 || character > 0x7e {
			notes = append(notes, "instance_hint must be at most 128 printable ASCII bytes")
			break
		}
	}
	if len(notes) > 0 {
		return nil, notes
	}
	return &Config{
		TrustDomain:        core.TrustDomain,
		EvidenceEndpoint:   endpoint,
		AttestationKeyPath: filepath.Clean(raw.AttestationKeyPath),
		EvidenceTimeout:    timeout,
		MaxEvidenceBytes:   raw.MaxEvidenceBytes,
		InstanceHint:       raw.InstanceHint,
	}, nil
}

func validateEndpoint(endpoint *url.URL) error {
	switch endpoint.Scheme {
	case "unix":
		if endpoint.Host != "" || !filepath.IsAbs(endpoint.Path) || endpoint.RawQuery != "" || endpoint.Fragment != "" {
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
			endpoint.Path = defaultEvidencePath
		}
	case "https":
		return fmt.Errorf("HTTPS evidence_endpoint is not supported on the Agent local channel; use unix or loopback HTTP")
	default:
		return fmt.Errorf("evidence_endpoint scheme must be unix or http")
	}
	return nil
}
