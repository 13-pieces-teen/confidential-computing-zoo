// argus-workload records and checks the real service process on its Linux host.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/workload/target"
	"os"
	"path/filepath"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
func run() error {
	action := flag.String("action", "check", "register or check")
	registration := flag.String("registration", "/run/argus-workload/target.json", "root-owned registration")
	container := flag.String("container", "", "full Docker container ID")
	policy := flag.String("policy", "", "fixed workload policy")
	config := flag.String("config", "/etc/openviking/ov.conf", "actual workload configuration path")
	port := flag.Int("port", 1933, "single IPv4 loopback service port")
	flag.Parse()
	switch *action {
	case "check":
		t, err := target.Load(*registration)
		if err != nil {
			return err
		}
		if err = target.Check(t); err != nil {
			return err
		}
		return json.NewEncoder(os.Stdout).Encode(t)
	case "register":
		if !filepath.IsAbs(*registration) {
			return fmt.Errorf("registration path must be absolute")
		}
		t, err := target.Register(context.Background(), *container, *policy, *config, *port)
		if err != nil {
			return err
		}
		// Never overwrite a live registration. Operator must stop Helper/NGINX and
		// remove the previous registration before registering a replacement launch.
		f, err := os.OpenFile(*registration, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if err != nil {
			return err
		}
		ok := false
		defer func() {
			_ = f.Close()
			if !ok {
				_ = os.Remove(*registration)
			}
		}()
		if err = json.NewEncoder(f).Encode(t); err != nil {
			return err
		}
		if err = f.Sync(); err != nil {
			return err
		}
		ok = true
		return json.NewEncoder(os.Stdout).Encode(t)
	default:
		return fmt.Errorf("unknown action")
	}
}
