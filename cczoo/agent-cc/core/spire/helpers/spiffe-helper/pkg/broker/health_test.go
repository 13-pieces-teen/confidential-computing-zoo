package broker

import (
	"context"
	"errors"
	"net"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestHealthRequiresCompletedCheckAndConsumerProgress(t *testing.T) {
	now := time.Now()
	h := newHealth(now, 2*time.Minute)
	if err := h.check(now); err == nil {
		t.Fatal("health accepted before a completed target check")
	}
	h.targetChecked(now)
	if err := h.check(now); err != nil {
		t.Fatal(err)
	}
	// A remote appraisal may legitimately take longer than WatchdogSec.
	later := now.Add(60 * time.Second)
	h.targetChecked(later)
	if err := h.check(later); err != nil {
		t.Fatalf("bounded initialization was mistaken for a stalled consumer: %v", err)
	}
	h.published(later, later.Add(time.Minute))
	later = later.Add(progressMaxAge + time.Nanosecond)
	h.targetChecked(later)
	if err := h.check(later); err == nil || !strings.Contains(err.Error(), "consumer") {
		t.Fatalf("healthy target hid a stuck consumer: %v", err)
	}
	h.loopProgress(later)
	if err := h.check(later); err != nil {
		t.Fatal(err)
	}
	later = later.Add(progressMaxAge + time.Nanosecond)
	h.loopProgress(later)
	if err := h.check(later); err == nil || !strings.Contains(err.Error(), "target check") {
		t.Fatalf("live consumer hid a stuck target check: %v", err)
	}
}

func TestHealthBudgetsDoNotRenewThemselves(t *testing.T) {
	now := time.Now()
	t.Run("startup", func(t *testing.T) {
		h := newHealth(now, time.Second)
		h.targetChecked(now.Add(time.Second))
		h.loopProgress(now.Add(time.Second))
		if err := h.check(now.Add(time.Second)); err == nil {
			t.Fatal("consumer tick extended initial attestation budget")
		}
	})
	t.Run("publication", func(t *testing.T) {
		h := newHealth(now, time.Minute)
		h.published(now, now.Add(time.Hour))
		h.publishing(now, now.Add(time.Hour))
		later := now.Add(hookTimeout + hookWaitDelay)
		h.targetChecked(later)
		h.loopProgress(later)
		if err := h.check(later); err == nil || !strings.Contains(err.Error(), "publication") {
			t.Fatalf("live loops extended stuck publication: %v", err)
		}
	})
	t.Run("expiry during rotation", func(t *testing.T) {
		h := newHealth(now, time.Minute)
		h.published(now, now.Add(time.Second))
		h.publishing(now, now.Add(time.Hour))
		h.targetChecked(now.Add(time.Second))
		if err := h.check(now.Add(time.Second)); err == nil || !strings.Contains(err.Error(), "expired") {
			t.Fatalf("publication excused an expired active certificate: %v", err)
		}
	})
	t.Run("rotation and successful initialization", func(t *testing.T) {
		h := newHealth(now, time.Second)
		h.published(now, now.Add(time.Hour))
		later := now.Add(2 * time.Second)
		h.targetChecked(later)
		h.loopProgress(later)
		if err := h.check(later); err != nil {
			t.Fatal(err)
		}
		h.publishing(later, now.Add(2*time.Hour))
		h.published(later, now.Add(2*time.Hour))
		if err := h.check(later); err != nil {
			t.Fatal(err)
		}
	})
}

func TestHealthNeverFeedsExpiredProgress(t *testing.T) {
	now := time.Now()
	h := newHealth(now, time.Minute)
	h.targetChecked(now.Add(-progressMaxAge - time.Second))
	feeds := 0
	var failure error
	runHealth(context.Background(), h, time.Millisecond, func() error { feeds++; return nil }, func(err error) { failure = err })
	if failure == nil || feeds != 0 {
		t.Fatalf("stale observation fed watchdog: feeds=%d error=%v", feeds, failure)
	}
	h.targetChecked(time.Now())
	want := errors.New("notification socket unavailable")
	runHealth(context.Background(), h, time.Millisecond, func() error { return want }, func(err error) { failure = err })
	if !errors.Is(failure, want) {
		t.Fatalf("notification failure ignored: %v", failure)
	}
}

func clearWatchdogEnvironment(t *testing.T) {
	t.Helper()
	for _, key := range []string{"WATCHDOG_USEC", "WATCHDOG_PID", "NOTIFY_SOCKET", "INVOCATION_ID", "ARGUS_REQUIRE_SYSTEMD_WATCHDOG"} {
		t.Setenv(key, "")
	}
}

func TestWatchdogEnvironmentFailsClosed(t *testing.T) {
	clearWatchdogEnvironment(t)
	w, err := watchdogFromEnvironment()
	if err != nil || w.notify != nil || len(w.invocationID) != 32 {
		t.Fatalf("standalone invocation unavailable: %+v %v", w, err)
	}
	t.Setenv("ARGUS_REQUIRE_SYSTEMD_WATCHDOG", "1")
	if _, err := watchdogFromEnvironment(); err == nil {
		t.Fatal("required watchdog silently disabled")
	}
	t.Setenv("WATCHDOG_USEC", "5000000")
	t.Setenv("INVOCATION_ID", strings.Repeat("a", 32))
	t.Setenv("NOTIFY_SOCKET", "/run/test-notify")
	t.Setenv("WATCHDOG_PID", strconv.Itoa(os.Getpid()+1))
	if _, err := watchdogFromEnvironment(); err == nil {
		t.Fatal("another process watchdog accepted")
	}
	t.Setenv("WATCHDOG_PID", strconv.Itoa(os.Getpid()))
	for _, value := range []string{"0", "-1", "oops", "9223372036854775807"} {
		t.Setenv("WATCHDOG_USEC", value)
		if _, err := watchdogFromEnvironment(); err == nil {
			t.Fatalf("invalid watchdog interval accepted: %s", value)
		}
	}
}

func TestWatchdogNotifiesWithoutReadinessOrAttestation(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("systemd notification uses Unix datagrams")
	}
	clearWatchdogEnvironment(t)
	socket := filepath.Join(t.TempDir(), "notify")
	listener, err := net.ListenUnixgram("unixgram", &net.UnixAddr{Name: socket, Net: "unixgram"})
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	t.Setenv("WATCHDOG_USEC", "5000000")
	t.Setenv("INVOCATION_ID", strings.Repeat("a", 32))
	t.Setenv("NOTIFY_SOCKET", socket)
	w, err := watchdogFromEnvironment()
	if err != nil {
		t.Fatal(err)
	}
	if err = w.notify(); err != nil {
		t.Fatal(err)
	}
	_ = listener.SetReadDeadline(time.Now().Add(time.Second))
	b := make([]byte, 128)
	n, _, err := listener.ReadFromUnix(b)
	if err != nil || string(b[:n]) != "WATCHDOG=1" {
		t.Fatalf("unexpected systemd message %q: %v", b[:n], err)
	}
}

func TestHookBoundsInheritedOutputPipes(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("requires Linux shell process semantics")
	}
	dir := t.TempDir()
	hook := filepath.Join(dir, "hook.sh")
	// The shell exits but a bounded child still owns its stdout. WaitDelay must
	// prevent waiting for that child's whole lifetime after the hook has exited.
	if err := os.WriteFile(hook, []byte("#!/bin/sh\nsleep 3 &\nexit 0\n"), 0700); err != nil {
		t.Fatal(err)
	}
	p := NewPublisher(dir, hook)
	start := time.Now()
	err := p.Hook(context.Background(), "publish")
	if err == nil || time.Since(start) > 2*time.Second {
		t.Fatalf("inherited hook output was not bounded: %v after %v", err, time.Since(start))
	}
}
