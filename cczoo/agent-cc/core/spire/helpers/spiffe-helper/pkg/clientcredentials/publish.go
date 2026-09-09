package clientcredentials

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/spiffe/spiffe-helper/pkg/broker"
)

type lease struct {
	Version    int    `json:"version"`
	Generation string `json:"generation"`
	Serial     string `json:"serial"`
	Expires    int64  `json:"expires_at"`
	Until      int64  `json:"lease_until"`
}

type publisher struct {
	dir     string
	gid     int
	current *lease
}

func (p *publisher) clear() error {
	p.current = nil
	entries, err := os.ReadDir(p.dir)
	if err != nil {
		return err
	}
	var result error
	for _, entry := range entries {
		if entry.Name() == "ready.json" || strings.HasPrefix(entry.Name(), "generation-") || strings.HasPrefix(entry.Name(), ".lease-") {
			result = errors.Join(result, os.RemoveAll(filepath.Join(p.dir, entry.Name())))
		}
	}
	return result
}

func (p *publisher) access(path string, mode os.FileMode) error {
	if err := os.Chmod(path, mode); err != nil {
		return err
	}
	if p.gid >= 0 {
		return os.Chown(path, -1, p.gid)
	}
	return nil
}

func (p *publisher) publish(c *broker.Credentials, now time.Time) error {
	if !now.Before(c.Expires) {
		return fmt.Errorf("cannot publish expired client SVID")
	}
	dir, err := os.MkdirTemp(p.dir, "generation-")
	if err != nil {
		return err
	}
	if err = p.access(dir, 0750); err != nil {
		return err
	}
	for name, data := range map[string][]byte{"svid.pem": c.Certificate, "key.pem": c.Key, "bundle.pem": c.Bundle} {
		path := filepath.Join(dir, name)
		if err = os.WriteFile(path, data, 0640); err != nil {
			return err
		}
		if err = p.access(path, 0640); err != nil {
			return err
		}
	}
	// Credentials.Serial is the decimal representation used by the server helper.
	// Use the certificate's hexadecimal serial for the native Node TLS client.
	serial, err := certificateSerial(c.Certificate)
	if err != nil {
		return err
	}
	p.current = &lease{Version: 1, Generation: filepath.Base(dir), Serial: serial, Expires: c.Expires.UnixMilli()}
	if err = p.renew(now); err != nil {
		return err
	}
	entries, err := os.ReadDir(p.dir)
	if err != nil {
		return err
	}
	for _, entry := range entries {
		if strings.HasPrefix(entry.Name(), "generation-") && entry.Name() != p.current.Generation {
			if err = os.RemoveAll(filepath.Join(p.dir, entry.Name())); err != nil {
				return err
			}
		}
	}
	return nil
}

func (p *publisher) renew(now time.Time) error {
	if p.current == nil {
		return nil
	}
	if now.UnixMilli() >= p.current.Expires {
		return fmt.Errorf("client SVID expired")
	}
	p.current.Until = min(now.Add(2500*time.Millisecond).UnixMilli(), p.current.Expires)
	data, err := json.Marshal(p.current)
	if err != nil {
		return err
	}
	f, err := os.CreateTemp(p.dir, ".lease-")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	if _, err = f.Write(data); err != nil {
		f.Close()
		return err
	}
	if err = f.Close(); err != nil {
		return err
	}
	if err = p.access(f.Name(), 0640); err != nil {
		return err
	}
	return os.Rename(f.Name(), filepath.Join(p.dir, "ready.json"))
}
