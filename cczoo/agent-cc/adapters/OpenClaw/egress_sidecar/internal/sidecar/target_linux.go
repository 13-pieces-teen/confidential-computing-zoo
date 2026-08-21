//go:build linux

package sidecar

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"syscall"

	"golang.org/x/sys/unix"
)

type TargetWatcher struct {
	fd        int
	closeOnce sync.Once
}

func OpenTarget(pid int) (*TargetWatcher, error) {
	if pid <= 0 {
		return nil, fmt.Errorf("target PID must be positive")
	}
	fd, err := unix.PidfdOpen(pid, 0)
	if err != nil {
		return nil, fmt.Errorf("open pidfd for OpenClaw PID %d: %w", pid, err)
	}
	return &TargetWatcher{fd: fd}, nil
}

func (watcher *TargetWatcher) Wait(ctx context.Context) error {
	for {
		pollFDs := []unix.PollFd{{Fd: int32(watcher.fd), Events: unix.POLLIN}}
		count, err := unix.Poll(pollFDs, 1000)
		if errors.Is(err, syscall.EINTR) {
			continue
		}
		if err != nil {
			return fmt.Errorf("poll OpenClaw pidfd: %w", err)
		}
		if count > 0 {
			return nil
		}
		if err := ctx.Err(); err != nil {
			return err
		}
	}
}

func (watcher *TargetWatcher) Close() error {
	var err error
	watcher.closeOnce.Do(func() { err = unix.Close(watcher.fd) })
	return err
}
