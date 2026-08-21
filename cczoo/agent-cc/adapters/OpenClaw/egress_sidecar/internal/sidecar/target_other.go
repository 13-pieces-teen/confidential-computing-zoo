//go:build !linux

package sidecar

import (
	"context"
	"fmt"
)

type TargetWatcher struct{}

func OpenTarget(int) (*TargetWatcher, error) {
	return nil, fmt.Errorf("OpenClaw Egress Broker requires Linux pidfd support")
}

func (*TargetWatcher) Wait(context.Context) error {
	return fmt.Errorf("OpenClaw Egress Broker requires Linux pidfd support")
}

func (*TargetWatcher) Close() error {
	return nil
}
