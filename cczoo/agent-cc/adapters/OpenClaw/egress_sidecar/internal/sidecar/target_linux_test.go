//go:build linux

package sidecar

import (
	"context"
	"os/exec"
	"testing"
	"time"
)

func TestTargetWatcherReturnsWhenTargetExits(t *testing.T) {
	command := exec.Command("sh", "-c", "read line")
	stdin, err := command.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() {
		_ = command.Process.Kill()
		_ = command.Wait()
	}()
	watcher, err := OpenTarget(command.Process.Pid)
	if err != nil {
		t.Fatal(err)
	}
	defer watcher.Close()
	if err := stdin.Close(); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := watcher.Wait(ctx); err != nil {
		t.Fatal(err)
	}
}
