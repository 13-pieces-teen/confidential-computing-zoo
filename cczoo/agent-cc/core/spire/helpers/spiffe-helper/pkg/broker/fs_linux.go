//go:build linux

package broker

import (
	"fmt"
	"golang.org/x/sys/unix"
	"os"
)

func checkTmpfs(path string) error {
	var fs unix.Statfs_t
	if err := unix.Statfs(path, &fs); err != nil {
		return err
	}
	if fs.Type != unix.TMPFS_MAGIC {
		return fmt.Errorf("credential directory must be on tmpfs")
	}
	st, err := os.Lstat(path)
	if err != nil {
		return err
	}
	var stat unix.Stat_t
	if err = unix.Lstat(path, &stat); err != nil {
		return err
	}
	if !st.IsDir() || st.Mode().Perm() != 0700 || stat.Uid != 0 {
		return fmt.Errorf("credential directory must be root-owned mode 0700")
	}
	return nil
}
