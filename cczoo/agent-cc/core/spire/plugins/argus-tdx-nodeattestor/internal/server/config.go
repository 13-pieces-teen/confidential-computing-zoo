package server

import (
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/hashicorp/hcl"
	configv1 "github.com/spiffe/spire-plugin-sdk/proto/spire/service/common/config/v1"

	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/internal/policy"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/internal/protocol"
)

const verifyPath = "/v1/verify/tdx-node"

type Config struct {
	TrustDomain             string
	TrusteeURL              *url.URL
	TrusteeExpectedSPIFFEID string
	TrusteeTLSConfig        *tls.Config
	Policy                  *policy.Policy
	ChallengeTTL            time.Duration
	VerifierTimeout         time.Duration
	MaxEvidenceBytes        int64
}

type hclConfig struct {
	TrusteeURL              string `hcl:"trustee_url"`
	TrusteeCAPath           string `hcl:"trustee_ca_path"`
	TrusteeClientCertPath   string `hcl:"trustee_client_cert_path"`
	TrusteeClientKeyPath    string `hcl:"trustee_client_key_path"`
	TrusteeServerName       string `hcl:"trustee_server_name"`
	TrusteeExpectedSPIFFEID string `hcl:"trustee_expected_spiffe_id"`
	TrusteeAuthMode         string `hcl:"trustee_auth_mode"`
	PolicyPath              string `hcl:"policy_path"`
	ChallengeTTL            string `hcl:"challenge_ttl"`
	VerifierTimeout         string `hcl:"verifier_timeout"`
	MaxEvidenceBytes        int64  `hcl:"max_evidence_bytes"`
}

func parseConfig(core *configv1.CoreConfiguration, input string) (*Config, []string) {
	raw := hclConfig{
		TrusteeAuthMode:  "mtls_files",
		ChallengeTTL:     "30s",
		VerifierTimeout:  "15s",
		MaxEvidenceBytes: protocol.MaxEvidenceSize,
	}
	var notes []string
	if err := hcl.Decode(&raw, input); err != nil {
		return nil, []string{fmt.Sprintf("decode HCL configuration: %v", err)}
	}
	if core == nil || core.TrustDomain == "" {
		notes = append(notes, "core trust_domain is required")
	}
	trusteeURL, err := url.Parse(raw.TrusteeURL)
	if err != nil || trusteeURL.Scheme != "https" || trusteeURL.Host == "" || trusteeURL.RawQuery != "" || trusteeURL.Fragment != "" {
		notes = append(notes, "trustee_url must be an HTTPS origin without query or fragment")
	} else {
		if trusteeURL.Path != "" && trusteeURL.Path != "/" {
			notes = append(notes, "trustee_url must not contain a path")
		}
		trusteeURL.Path = verifyPath
	}
	if raw.TrusteeAuthMode != "mtls_files" {
		notes = append(notes, "trustee_auth_mode must be mtls_files")
	}
	if raw.TrusteeServerName == "" || strings.ContainsAny(raw.TrusteeServerName, "/:@") {
		notes = append(notes, "trustee_server_name is invalid")
	}
	if err := validateSPIFFEID(raw.TrusteeExpectedSPIFFEID, core); err != nil {
		notes = append(notes, err.Error())
	}
	challengeTTL, err := time.ParseDuration(raw.ChallengeTTL)
	if err != nil || challengeTTL < time.Second || challengeTTL > 2*time.Minute {
		notes = append(notes, "challenge_ttl must be between 1s and 2m")
	}
	verifierTimeout, err := time.ParseDuration(raw.VerifierTimeout)
	if err != nil || verifierTimeout <= 0 || verifierTimeout > 30*time.Second {
		notes = append(notes, "verifier_timeout must be greater than zero and no more than 30s")
	} else if challengeTTL > 0 && verifierTimeout >= challengeTTL {
		notes = append(notes, "verifier_timeout must be shorter than challenge_ttl")
	}
	if raw.MaxEvidenceBytes <= 0 || raw.MaxEvidenceBytes > protocol.MaxEvidenceSize {
		notes = append(notes, fmt.Sprintf("max_evidence_bytes must be between 1 and %d", protocol.MaxEvidenceSize))
	}

	loadedPolicy, policyErr := loadPolicy(raw.PolicyPath)
	if policyErr != nil {
		notes = append(notes, policyErr.Error())
	}
	tlsConfig, tlsErr := loadTLSConfig(raw)
	if tlsErr != nil {
		notes = append(notes, tlsErr.Error())
	}
	if len(notes) > 0 {
		return nil, notes
	}
	return &Config{
		TrustDomain:             core.TrustDomain,
		TrusteeURL:              trusteeURL,
		TrusteeExpectedSPIFFEID: raw.TrusteeExpectedSPIFFEID,
		TrusteeTLSConfig:        tlsConfig,
		Policy:                  loadedPolicy,
		ChallengeTTL:            challengeTTL,
		VerifierTimeout:         verifierTimeout,
		MaxEvidenceBytes:        raw.MaxEvidenceBytes,
	}, nil
}

func loadPolicy(path string) (*policy.Policy, error) {
	if !filepath.IsAbs(path) {
		return nil, fmt.Errorf("policy_path must be absolute")
	}
	if err := requireRegularFile(path, false); err != nil {
		return nil, fmt.Errorf("policy_path: %w", err)
	}
	loaded, err := policy.Load(path)
	if err != nil {
		return nil, fmt.Errorf("policy_path: %w", err)
	}
	return loaded, nil
}

func loadTLSConfig(raw hclConfig) (*tls.Config, error) {
	for name, path := range map[string]string{
		"trustee_ca_path":          raw.TrusteeCAPath,
		"trustee_client_cert_path": raw.TrusteeClientCertPath,
		"trustee_client_key_path":  raw.TrusteeClientKeyPath,
	} {
		if !filepath.IsAbs(path) {
			return nil, fmt.Errorf("%s must be absolute", name)
		}
	}
	if err := requireRegularFile(raw.TrusteeCAPath, false); err != nil {
		return nil, fmt.Errorf("trustee_ca_path: %w", err)
	}
	if err := requireRegularFile(raw.TrusteeClientCertPath, false); err != nil {
		return nil, fmt.Errorf("trustee_client_cert_path: %w", err)
	}
	if err := requireRegularFile(raw.TrusteeClientKeyPath, true); err != nil {
		return nil, fmt.Errorf("trustee_client_key_path: %w", err)
	}
	caPEM, err := os.ReadFile(raw.TrusteeCAPath)
	if err != nil {
		return nil, fmt.Errorf("read trustee CA: %w", err)
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(caPEM) {
		return nil, fmt.Errorf("trustee_ca_path contains no certificates")
	}
	certificate, err := tls.LoadX509KeyPair(raw.TrusteeClientCertPath, raw.TrusteeClientKeyPath)
	if err != nil {
		return nil, fmt.Errorf("load Trustee client key pair: %w", err)
	}
	return &tls.Config{
		MinVersion:   tls.VersionTLS13,
		RootCAs:      roots,
		Certificates: []tls.Certificate{certificate},
		ServerName:   raw.TrusteeServerName,
	}, nil
}

func requireRegularFile(path string, private bool) error {
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() {
		return fmt.Errorf("must be a regular file")
	}
	if private && info.Mode().Perm() != 0o600 {
		return fmt.Errorf("permissions must be 0600")
	}
	return nil
}

func validateSPIFFEID(value string, core *configv1.CoreConfiguration) error {
	parsed, err := url.Parse(value)
	if err != nil || parsed.Scheme != "spiffe" || parsed.Host == "" || parsed.Path == "" || parsed.RawQuery != "" || parsed.Fragment != "" || parsed.String() != value {
		return fmt.Errorf("trustee_expected_spiffe_id is invalid")
	}
	if core != nil && core.TrustDomain != "" && parsed.Host != core.TrustDomain {
		return fmt.Errorf("trustee_expected_spiffe_id must belong to the configured trust domain")
	}
	return nil
}
