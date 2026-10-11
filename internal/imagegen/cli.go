// Copyright (C) 2026 luckygopher
// SPDX-License-Identifier: GPL-3.0-only
package imagegen

import (
	"fmt"
	"strconv"
	"strings"
)

type Options struct {
	Command, Prompt, PromptFile, Model, Size, Quality, Format, Background, OutputDir, Mask string
	References                                                                             []string
	Count, Timeout                                                                         int
	FromCodex, Overwrite, NoOpen                                                           bool
}

func parse(args []string) (Options, error) {
	options := Options{Count: 1}
	if len(args) == 0 {
		return options, fail("invalid_argument", "请使用 setup、status、doctor、generate 或 edit。")
	}
	options.Command = args[0]
	if options.Command == "--version" || options.Command == "version" || options.Command == "--help" || options.Command == "-h" {
		return options, nil
	}
	switch options.Command {
	case "setup":
		options.Timeout = 600
	case "doctor":
		options.Timeout = 10
	case "status", "configure", "generate", "edit":
	default:
		return options, fail("invalid_argument", "未知命令。")
	}
	var positional []string
	for i := 1; i < len(args); i++ {
		argument := args[i]
		if argument == "--help" || argument == "-h" {
			options.Command = "--help"
			return options, nil
		}
		if !strings.HasPrefix(argument, "--") {
			positional = append(positional, argument)
			continue
		}
		name, value, assigned := strings.Cut(argument, "=")
		switch name {
		case "--from-codex":
			options.FromCodex = true
			continue
		case "--overwrite":
			options.Overwrite = true
			continue
		case "--no-open":
			options.NoOpen = true
			continue
		}
		if !assigned {
			i++
			if i >= len(args) {
				return options, fail("invalid_argument", "选项缺少参数："+name)
			}
			value = args[i]
		}
		switch name {
		case "--model":
			options.Model = value
		case "--size":
			options.Size = value
		case "--quality":
			options.Quality = value
		case "--format":
			options.Format = value
		case "--background":
			options.Background = value
		case "--output-dir":
			options.OutputDir = value
		case "--prompt-file":
			options.PromptFile = value
		case "--mask":
			options.Mask = value
		case "--reference":
			options.References = append(options.References, value)
		case "--count":
			number, err := strconv.Atoi(value)
			if err != nil {
				return options, fail("invalid_argument", "图片数量必须是整数。")
			}
			options.Count = number
		case "--timeout":
			number, err := strconv.Atoi(value)
			if err != nil {
				return options, fail("invalid_argument", "超时必须是整数秒。")
			}
			options.Timeout = number
		default:
			return options, fail("invalid_argument", "未知选项："+name)
		}
	}
	if options.Command == "generate" || options.Command == "edit" {
		if options.PromptFile != "" {
			if len(positional) > 0 {
				return options, fail("invalid_argument", "使用 --prompt-file 时请用 --reference 指定参考图。")
			}
		} else if len(positional) > 0 {
			options.Prompt = positional[0]
			positional = positional[1:]
		}
		if options.Command == "edit" {
			options.References = append(options.References, positional...)
		} else if len(positional) > 0 || len(options.References) > 0 || options.Mask != "" {
			return options, fail("invalid_argument", "参考图和蒙版请使用 edit 命令。")
		}
	} else if len(positional) > 0 {
		return options, fail("invalid_argument", "该命令不接受位置参数。")
	}
	for name, pair := range map[string][]string{"quality": {options.Quality, "auto", "low", "medium", "high"}, "format": {options.Format, "png", "jpeg", "webp"}, "background": {options.Background, "auto", "opaque", "transparent"}} {
		if pair[0] == "" {
			continue
		}
		valid := false
		for _, allowed := range pair[1:] {
			if pair[0] == allowed {
				valid = true
			}
		}
		if !valid {
			return options, fail("invalid_argument", "无效的 "+name+" 选项。")
		}
	}
	return options, nil
}

func (a *App) Run(args []string) int {
	options, err := parse(args)
	if err != nil {
		encode(a.Err, safeError(err, ""))
		return 1
	}
	var result any
	switch options.Command {
	case "version", "--version":
		encode(a.Out, map[string]any{"version": a.Version, "runtime": "native"})
		return 0
	case "--help", "-h":
		fmt.Fprintln(a.Out, "Image Gen: setup | status | doctor | configure --from-codex | generate PROMPT | edit PROMPT REFERENCES\n选项: --model --size --quality --count --format --background --output-dir --prompt-file --reference --mask\n用户无需 Python、Node 或 Go。setup 打开本机配置页面，保存后自动退出。")
		return 0
	case "status":
		result, err = a.Status()
	case "doctor":
		result, err = a.Doctor(options.Timeout)
	case "setup":
		err = a.Setup(!options.NoOpen, options.Timeout)
		if err == nil {
			return 0
		}
	case "configure":
		if options.FromCodex {
			result, err = a.importedConfig(options.Model, options.Overwrite)
		} else {
			err = a.Setup(!options.NoOpen, 600)
			if err == nil {
				return 0
			}
		}
	case "generate", "edit":
		result, err = a.Generate(options)
	}
	if err != nil {
		key := ""
		if cfg, _, loadErr := a.Load(false); loadErr == nil {
			key = cfg.APIKey
		}
		encode(a.Err, safeError(err, key))
		return 1
	}
	encode(a.Out, result)
	if object, ok := result.(map[string]any); ok {
		if object["status"] == "partial" || object["status"] == "error" {
			return 1
		}
	}
	return 0
}
