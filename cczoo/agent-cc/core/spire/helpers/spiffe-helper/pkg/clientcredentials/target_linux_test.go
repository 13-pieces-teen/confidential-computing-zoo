//go:build linux

package clientcredentials

import (
	"context"
	"os/exec"
	"testing"
	"time"
)

func TestActualPIDFDAndInstanceMismatch(t *testing.T) {
	process := exec.Command("sleep", "30")
	if err := process.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() {
		_ = process.Process.Kill()
		_ = process.Wait()
	}()
	target, err := observe(process.Process.Pid)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	wrong := target
	wrong.StartTime += "0"
	if _, err = watch(ctx, wrong); err == nil {
		t.Fatal("changed PID instance accepted")
	}
	ended, err := watch(ctx, target)
	if err != nil {
		t.Fatal(err)
	}
	if err = process.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	select {
	case <-ended:
	case <-time.After(time.Second):
		t.Fatal("target exit was not observed")
	}
}
