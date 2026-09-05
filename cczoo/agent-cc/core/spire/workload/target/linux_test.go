//go:build linux

package target

import (
	"context"
	"encoding/json"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/workload/protocol"
	"golang.org/x/sys/unix"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"
)

func TestPidfdObservesRealProcessExit(t *testing.T) {
	cmd := exec.Command("sleep", "30")
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer cmd.Process.Kill()
	fd, err := unix.PidfdOpen(cmd.Process.Pid, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer unix.Close(fd)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	result := make(chan error, 1)
	go func() { result <- watchFD(ctx, protocol.Target{}, fd, func(protocol.Target) error { return nil }) }()
	if err := cmd.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	_ = cmd.Wait()
	select {
	case err := <-result:
		if err == nil {
			t.Fatal("exit not detected")
		}
	case <-time.After(time.Second):
		t.Fatal("pidfd exit observation delayed")
	}
}
func TestRegistrationRequiresProtectedDirectoryAndRegularFile(t *testing.T) {
	if os.Getuid() != 0 {
		t.Skip("root-owned deployment contract")
	}
	b, err := os.ReadFile("../testdata/runtime-data.json")
	if err != nil {
		t.Fatal(err)
	}
	var v struct {
		RuntimeData protocol.RuntimeData `json:"runtime_data"`
	}
	if err = json.Unmarshal(b, &v); err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	path := filepath.Join(dir, "target.json")
	b, _ = json.Marshal(v.RuntimeData.Target)
	if err = os.WriteFile(path, b, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err = Load(path); err != nil {
		t.Fatal(err)
	}
	if err = os.Chmod(dir, 0777); err != nil {
		t.Fatal(err)
	}
	if _, err = Load(path); err == nil {
		t.Fatal("unprotected registration directory accepted")
	}
	if err = os.Chmod(dir, 0700); err != nil {
		t.Fatal(err)
	}
	alias := filepath.Join(dir, "alias")
	if err = os.Symlink(path, alias); err != nil {
		t.Fatal(err)
	}
	if _, err = Load(alias); err == nil {
		t.Fatal("symlink registration accepted")
	}
}
