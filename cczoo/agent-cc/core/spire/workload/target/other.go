//go:build !linux

package target

import (
	"context"
	"fmt"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/workload/protocol"
	"os"
)

func checkOwner(os.FileInfo) error { return nil }
func Check(protocol.Target) error  { return fmt.Errorf("target validation requires Linux") }
func Register(context.Context, string, string, string, int) (protocol.Target, error) {
	return protocol.Target{}, fmt.Errorf("target registration requires Linux")
}
func StartWatch(context.Context, protocol.Target) (<-chan error, error) {
	return nil, fmt.Errorf("target watching requires Linux")
}
func Watch(context.Context, protocol.Target) error {
	return fmt.Errorf("pidfd monitoring requires Linux")
}
