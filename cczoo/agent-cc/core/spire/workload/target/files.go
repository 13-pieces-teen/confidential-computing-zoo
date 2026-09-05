package target

import (
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/workload/protocol"
)

func Load(path string) (protocol.Target, error) {
	var result protocol.Target
	info, err := os.Lstat(path)
	if err != nil {
		return result, err
	}
	if !info.Mode().IsRegular() || info.Size() > 32768 {
		return result, fmt.Errorf("target registration must be a small regular file")
	}
	if err = checkOwner(info); err != nil {
		return result, err
	}
	parent, err := os.Stat(filepath.Dir(path))
	if err != nil {
		return result, err
	}
	if err = checkOwner(parent); err != nil {
		return result, err
	}
	f, err := os.Open(path)
	if err != nil {
		return result, err
	}
	defer f.Close()
	actual, err := f.Stat()
	if err != nil {
		return result, err
	}
	if !os.SameFile(info, actual) {
		return result, fmt.Errorf("registration changed while opening")
	}
	b, err := io.ReadAll(io.LimitReader(f, 32769))
	if err != nil {
		return result, err
	}
	if len(b) > 32768 {
		return result, fmt.Errorf("registration exceeds limit")
	}
	if err = protocol.Decode(b, &result); err != nil {
		return result, err
	}
	return result, result.Validate()
}
