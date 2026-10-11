//go:build !windows

package imagegen

import "os"

func replaceConfig(source, destination string) error { return os.Rename(source, destination) }
