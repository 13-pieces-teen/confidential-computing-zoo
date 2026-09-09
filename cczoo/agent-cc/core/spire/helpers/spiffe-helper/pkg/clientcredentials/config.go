// Package clientcredentials delivers a local OpenClaw process's Broker SVID to
// its native TLS client. It does not proxy business traffic or appraise evidence.
package clientcredentials

import (
	"encoding/json"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"

	"github.com/spiffe/go-spiffe/v2/spiffeid"
)

type Config struct {
	WorkloadAPI  string `json:"workload_api_address"`
	BrokerSocket string `json:"broker_socket"`
	HelperID     string `json:"helper_spiffe_id"`
	AgentID      string `json:"agent_spiffe_id"`
	TargetID     string `json:"target_spiffe_id"`
	Registration string `json:"target_registration_path"`
	Directory    string `json:"credentials_dir"`
	ReaderGID    int    `json:"reader_gid"`
}

func LoadConfig(path string) (Config, error) {
	var c Config
	if err := protected(path, false); err != nil {
		return c, err
	}
	f, err := os.Open(path)
	if err != nil {
		return c, err
	}
	defer f.Close()
	d := json.NewDecoder(io.LimitReader(f, 16385))
	d.DisallowUnknownFields()
	if err = d.Decode(&c); err != nil {
		return c, err
	}
	if err = d.Decode(&struct{}{}); err != io.EOF {
		return c, fmt.Errorf("trailing configuration data")
	}
	return c, c.validate()
}

func (c Config) validate() error {
	if c.ReaderGID <= 0 {
		return fmt.Errorf("reader_gid must be a dedicated non-root host GID")
	}
	for _, path := range []string{c.BrokerSocket, c.Registration, c.Directory} {
		if !filepath.IsAbs(path) || filepath.Clean(path) == string(filepath.Separator) {
			return fmt.Errorf("absolute non-root paths required")
		}
	}
	endpoint, err := url.Parse(c.WorkloadAPI)
	if err != nil || endpoint.Scheme != "unix" || endpoint.Host != "" || endpoint.User != nil || endpoint.RawQuery != "" || endpoint.Fragment != "" || !filepath.IsAbs(endpoint.Path) {
		return fmt.Errorf("workload_api_address must be unix:///absolute/socket")
	}
	ids := map[string]bool{}
	for _, value := range []string{c.HelperID, c.AgentID, c.TargetID} {
		id, err := spiffeid.FromString(value)
		if err != nil || id.Path() == "" || ids[value] {
			return fmt.Errorf("three distinct valid SPIFFE IDs required")
		}
		ids[value] = true
	}
	return nil
}

type Target struct {
	PID          int    `json:"pid"`
	StartTime    string `json:"start_time"`
	BootID       string `json:"boot_id"`
	PIDNamespace string `json:"pid_namespace"`
}

func Register(c Config, pid int) error {
	if err := protected(filepath.Dir(c.Registration), true); err != nil {
		return err
	}
	t, err := observe(pid)
	if err != nil {
		return err
	}
	f, err := os.OpenFile(c.Registration, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return err
	}
	defer f.Close()
	return json.NewEncoder(f).Encode(t)
}
