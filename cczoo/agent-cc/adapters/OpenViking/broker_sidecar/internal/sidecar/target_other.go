//go:build !linux

package sidecar

import (
	"context"
	"fmt"
)

type TargetWatcher struct{}

func OpenTarget(int) (*TargetWatcher, error) {
	return nil, fmt.Errorf("OpenViking Broker Sidecar requires Linux pidfd support")
}

func (*TargetWatcher) Wait(context.Context) error {
	return fmt.Errorf("OpenViking Broker Sidecar requires Linux pidfd support")
}

func (*TargetWatcher) Close() error {
	return nil
}
