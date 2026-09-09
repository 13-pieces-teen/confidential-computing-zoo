//go:build !linux

package clientcredentials

import (
	"context"
	"fmt"
)

func protected(string, bool) error { return fmt.Errorf("credential publisher requires Linux") }
func checkFilesystem(string) error { return fmt.Errorf("credential publisher requires Linux tmpfs") }
func observe(int) (Target, error) {
	return Target{}, fmt.Errorf("credential publisher requires Linux pidfd")
}
func watch(context.Context, Target) (<-chan error, error) {
	return nil, fmt.Errorf("credential publisher requires Linux pidfd")
}
