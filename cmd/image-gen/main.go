// Copyright (C) 2026 luckygopher
// SPDX-License-Identifier: GPL-3.0-only
package main

import (
	"github.com/luckygopher/agent-toolkit/internal/imagegen"
	"os"
)

var version = "0.3.0-dev"

func main() { os.Exit(imagegen.New(version, os.Stdout, os.Stderr).Run(os.Args[1:])) }
