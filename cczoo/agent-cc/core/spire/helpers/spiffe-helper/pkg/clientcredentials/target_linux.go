//go:build linux

package clientcredentials

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"

	"golang.org/x/sys/unix"
)

func protected(path string, directory bool) error {
	if !filepath.IsAbs(path) {
		return fmt.Errorf("absolute protected path required")
	}
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || stat.Uid != 0 || info.Mode().Perm()&0022 != 0 || info.Mode()&os.ModeSymlink != 0 || info.IsDir() != directory || (!directory && !info.Mode().IsRegular()) {
		return fmt.Errorf("%s must be root-owned and not writable by group/others", path)
	}
	if path != "/" {
		return protected(filepath.Dir(path), true)
	}
	return nil
}

func checkFilesystem(path string) error {
	if os.Geteuid() != 0 {
		return fmt.Errorf("credential publisher must run as root")
	}
	if err := protected(path, true); err != nil {
		return err
	}
	var stat unix.Statfs_t
	if err := unix.Statfs(path, &stat); err != nil {
		return err
	}
	if uint64(stat.Type) != unix.TMPFS_MAGIC {
		return fmt.Errorf("credentials directory must be on tmpfs")
	}
	return nil
}

func observe(pid int) (Target, error) {
	var result Target
	if pid <= 0 {
		return result, fmt.Errorf("target PID must be positive")
	}
	prefix := fmt.Sprintf("/proc/%d/", pid)
	data, err := os.ReadFile(prefix + "stat")
	if err != nil {
		return result, err
	}
	end := strings.LastIndex(string(data), ")")
	if end < 0 {
		return result, fmt.Errorf("invalid process stat")
	}
	fields := strings.Fields(string(data)[end+1:])
	if len(fields) < 20 || fields[0] == "Z" || fields[0] == "X" {
		return result, fmt.Errorf("target process is not live")
	}
	if _, err := strconv.ParseUint(fields[19], 10, 64); err != nil {
		return result, err
	}
	boot, err := os.ReadFile("/proc/sys/kernel/random/boot_id")
	if err != nil {
		return result, err
	}
	namespace, err := os.Readlink(prefix + "ns/pid")
	if err != nil {
		return result, err
	}
	return Target{pid, fields[19], strings.TrimSpace(string(boot)), namespace}, nil
}

func watch(ctx context.Context, target Target) (<-chan error, error) {
	before, err := observe(target.PID)
	if err != nil || before != target {
		return nil, fmt.Errorf("registered OpenClaw instance changed")
	}
	fd, err := unix.PidfdOpen(target.PID, 0)
	if err != nil {
		return nil, err
	}
	after, err := observe(target.PID)
	if err != nil || after != target {
		unix.Close(fd)
		return nil, fmt.Errorf("target changed while opening pidfd")
	}
	result := make(chan error, 1)
	go func() {
		defer unix.Close(fd)
		for ctx.Err() == nil {
			fds := []unix.PollFd{{Fd: int32(fd), Events: unix.POLLIN}}
			_, err := unix.Poll(fds, 250)
			if err == unix.EINTR {
				continue
			}
			if err != nil || fds[0].Revents != 0 {
				result <- fmt.Errorf("OpenClaw target exited: %v", err)
				return
			}
			current, err := observe(target.PID)
			if err != nil || current != target {
				result <- fmt.Errorf("OpenClaw instance changed")
				return
			}
		}
	}()
	return result, nil
}
