package broker

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// Publisher owns its entire private directory. Each generation contains all
// three files before the current symlink switches. NGINX reload opens one
// generation; the hook validates the config before starting/reloading it.
type Publisher struct {
	Dir     string
	Hook    func(context.Context, string) error
	checkFS func(string) error
}

func NewPublisher(dir, hook string) *Publisher {
	return &Publisher{Dir: dir, checkFS: checkTmpfs, Hook: func(ctx context.Context, action string) error {
		ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
		defer cancel()
		out, err := exec.CommandContext(ctx, hook, action).CombinedOutput()
		if err != nil {
			return fmt.Errorf("NGINX %s hook: %w: %s", action, err, out)
		}
		return nil
	}}
}
func (p *Publisher) Prepare() error {
	if err := os.MkdirAll(p.Dir, 0700); err != nil {
		return err
	}
	if err := p.checkFS(p.Dir); err != nil {
		return err
	}
	return p.Clear()
}
func (p *Publisher) Clear() error {
	entries, err := os.ReadDir(p.Dir)
	if err != nil {
		return err
	}
	var errs []error
	for _, e := range entries {
		if e.Name() == "current" || e.Name() == "ready" || e.Name() == ".ready-next" || e.Name() == ".next" || strings.HasPrefix(e.Name(), "generation-") {
			// Only our direct children are removed; RemoveAll never follows symlinks.
			errs = append(errs, os.RemoveAll(filepath.Join(p.Dir, e.Name())))
		}
	}
	return errors.Join(errs...)
}
func (p *Publisher) Publish(ctx context.Context, c *Credentials) error {
	ctx, cancel := context.WithDeadline(ctx, c.Expires)
	defer cancel()
	if err := ctx.Err(); err != nil {
		return fmt.Errorf("credentials expired before publication: %w", err)
	}
	generation, err := os.MkdirTemp(p.Dir, "generation-")
	if err != nil {
		return err
	}
	published := false
	defer func() {
		if !published {
			_ = os.RemoveAll(generation)
		}
	}()
	for name, data := range map[string][]byte{"svid.pem": c.Certificate, "key.pem": c.Key, "bundle.pem": c.Bundle} {
		if err = os.WriteFile(filepath.Join(generation, name), data, 0600); err != nil {
			return err
		}
	}
	next := filepath.Join(p.Dir, ".next")
	if err = os.Symlink(filepath.Base(generation), next); err != nil {
		return err
	}
	if err = os.Rename(next, filepath.Join(p.Dir, "current")); err != nil {
		_ = os.Remove(next)
		return err
	}
	if err = p.Hook(ctx, "publish"); err != nil {
		_ = p.Clear()
		return err
	}
	if err = ctx.Err(); err != nil {
		_ = p.Clear()
		return fmt.Errorf("credentials expired or publication canceled: %w", err)
	}
	readyNext := filepath.Join(p.Dir, ".ready-next")
	if err = os.WriteFile(readyNext, []byte(c.Serial+"\n"), 0600); err != nil {
		_ = p.Clear()
		return err
	}
	if err = os.Rename(readyNext, filepath.Join(p.Dir, "ready")); err != nil {
		_ = p.Clear()
		return err
	}
	published = true
	entries, err := os.ReadDir(p.Dir)
	if err != nil {
		return err
	}
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), "generation-") && e.Name() != filepath.Base(generation) {
			if err = os.RemoveAll(filepath.Join(p.Dir, e.Name())); err != nil {
				return err
			}
		}
	}
	return nil
}
