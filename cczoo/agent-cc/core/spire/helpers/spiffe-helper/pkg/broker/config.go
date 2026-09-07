// Package broker adds an opt-in, local PID Broker consumer to SPIFFE Helper.
package broker

import (
	"fmt"
	"github.com/hashicorp/hcl/hcl/token"
	"github.com/spiffe/go-spiffe/v2/spiffeid"
	"net/url"
	"path/filepath"
	"runtime"
)

type Config struct {
	Endpoint               string                 `hcl:"endpoint"`
	HelperSPIFFEID         string                 `hcl:"helper_spiffe_id"`
	AgentSPIFFEID          string                 `hcl:"agent_spiffe_id"`
	TargetSPIFFEID         string                 `hcl:"target_spiffe_id"`
	TargetRegistrationPath string                 `hcl:"target_registration_path"`
	PublishHook            string                 `hcl:"publish_hook"`
	UnusedKeyPositions     map[string][]token.Pos `hcl:",unusedKeyPositions"`
}

func (c Config) Validate(certDir string) error {
	if runtime.GOOS != "linux" {
		return fmt.Errorf("Broker mode requires Linux pidfd")
	}
	ep, err := url.Parse(c.Endpoint)
	if err != nil || ep.Scheme != "unix" || ep.Host != "" || ep.User != nil || !filepath.IsAbs(ep.Path) || ep.RawQuery != "" || ep.Fragment != "" {
		return fmt.Errorf("broker endpoint must be unix:///absolute/socket")
	}
	for _, p := range []string{certDir, c.TargetRegistrationPath, c.PublishHook} {
		if !filepath.IsAbs(p) {
			return fmt.Errorf("broker paths must be absolute")
		}
	}
	for _, id := range []string{c.HelperSPIFFEID, c.AgentSPIFFEID, c.TargetSPIFFEID} {
		if _, err := spiffeid.FromString(id); err != nil {
			return err
		}
	}
	if c.HelperSPIFFEID == c.TargetSPIFFEID || c.HelperSPIFFEID == c.AgentSPIFFEID || c.TargetSPIFFEID == c.AgentSPIFFEID {
		return fmt.Errorf("Helper, Agent and target identities must be distinct")
	}
	if len(c.UnusedKeyPositions) > 0 {
		return fmt.Errorf("unknown broker configuration")
	}
	return nil
}
