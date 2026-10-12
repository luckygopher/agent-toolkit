// Copyright (C) 2026 luckygopher
// SPDX-License-Identifier: GPL-3.0-only
package imagegen

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"runtime"
	"strings"
	"sync"
	"time"
)

//go:embed web/setup.html
var setupPage string

type setupSession struct {
	app                                                *App
	server                                             *http.Server
	listener                                           net.Listener
	origin, prefix, token, nonce, keyAddress, original string
	cfg                                                Config
	sources                                            map[string]string
	saved                                              bool
	mu                                                 sync.Mutex
	once                                               sync.Once
	done                                               chan struct{}
	ctx                                                context.Context
	cancel                                             context.CancelFunc
}

func fingerprint(path string) (string, error) {
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(data)
	return hex.EncodeToString(digest[:]), nil
}

func (a *App) newSetup(lifetime time.Duration) (*setupSession, error) {
	cfg, sources, err := a.Load(false)
	if err != nil {
		return nil, err
	}
	original, err := fingerprint(a.ConfigPath())
	if err != nil {
		return nil, fail("config_read_error", "无法读取本机配置。")
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, fail("setup_error", "无法启动本机配置页面，请检查执行环境是否允许监听本机端口。")
	}
	ctx, cancel := context.WithCancel(context.Background())
	session := &setupSession{app: a, listener: listener, origin: "http://" + listener.Addr().String(), token: randomToken(32), nonce: randomToken(24), keyAddress: cfg.BaseURL, original: original, cfg: cfg, sources: sources, done: make(chan struct{}), ctx: ctx, cancel: cancel}
	session.prefix = "/setup/" + session.token + "/"
	session.server = &http.Server{Handler: session, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second, WriteTimeout: 10 * time.Second, IdleTimeout: 10 * time.Second, MaxHeaderBytes: 16 * 1024, ErrorLog: log.New(io.Discard, "", 0)}
	go func() {
		if err := session.server.Serve(listener); err != nil && !errors.Is(err, http.ErrServerClosed) {
			session.Close()
		}
	}()
	go func() {
		timer := time.NewTimer(lifetime)
		defer timer.Stop()
		select {
		case <-timer.C:
			session.Close()
		case <-ctx.Done():
		}
	}()
	return session, nil
}

func (s *setupSession) Close() {
	s.once.Do(func() {
		s.cancel()
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		_ = s.server.Shutdown(ctx)
		_ = s.server.Close()
		s.mu.Lock()
		s.cfg.APIKey = ""
		s.mu.Unlock()
		close(s.done)
	})
}
func (s *setupSession) stopAfter(delay time.Duration) {
	go func() {
		timer := time.NewTimer(delay)
		defer timer.Stop()
		select {
		case <-timer.C:
			s.Close()
		case <-s.ctx.Done():
		}
	}()
}

func (s *setupSession) reply(w http.ResponseWriter, code int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Referrer-Policy", "no-referrer")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("X-Frame-Options", "DENY")
	w.Header().Set("Content-Security-Policy", "default-src 'none'; script-src 'nonce-"+s.nonce+"'; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
	if page, ok := value.(string); ok {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.WriteHeader(code)
		_, _ = io.WriteString(w, page)
		return
	}
	w.WriteHeader(code)
	encode(w, value)
}

func (s *setupSession) public() map[string]any {
	overrides := []string{}
	for key, source := range s.sources {
		if strings.HasPrefix(source, "IMAGE_GEN_") {
			overrides = append(overrides, key)
		}
	}
	return map[string]any{"base_url": s.cfg.BaseURL, "model": s.cfg.Model, "size": s.cfg.Size, "timeout": s.cfg.Timeout, "output_dir": s.cfg.OutputDir, "api_key_configured": s.cfg.APIKey != "", "existing_configuration": s.original != "", "environment_overrides": overrides}
}

func (s *setupSession) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if "http://"+r.Host != s.origin {
		s.reply(w, 403, map[string]any{"error": "无效的本机页面地址。"})
		return
	}
	if !strings.HasPrefix(r.URL.Path, s.prefix) {
		s.reply(w, 404, map[string]any{"error": "配置链接无效或已过期。"})
		return
	}
	route := strings.TrimPrefix(r.URL.Path, s.prefix)
	s.mu.Lock()
	defer s.mu.Unlock()
	if r.Method == http.MethodGet {
		switch route {
		case "":
			s.reply(w, 200, strings.ReplaceAll(setupPage, "__NONCE__", s.nonce))
		case "state":
			s.reply(w, 200, s.public())
		default:
			s.reply(w, 404, map[string]any{"error": "页面不存在。"})
		}
		return
	}
	if r.Method != http.MethodPost {
		s.reply(w, 405, map[string]any{"error": "不支持的操作。"})
		return
	}
	if r.Header.Get("Origin") != s.origin || subtle.ConstantTimeCompare([]byte(r.Header.Get("X-Setup-Token")), []byte(s.token)) != 1 {
		s.reply(w, 403, map[string]any{"error": "只能从本机配置页面操作。"})
		return
	}
	if strings.Split(r.Header.Get("Content-Type"), ";")[0] != "application/json" {
		s.reply(w, 415, map[string]any{"error": "配置格式无效。"})
		return
	}
	data, err := limitRead(r.Body, 16*1024)
	if err != nil {
		s.reply(w, 413, map[string]any{"error": "配置内容过大。"})
		return
	}
	var payload struct {
		BaseURL   string  `json:"base_url"`
		APIKey    string  `json:"api_key"`
		Model     *string `json:"model"`
		Size      *string `json:"size"`
		Timeout   *int    `json:"timeout"`
		OutputDir *string `json:"output_dir"`
		Overwrite bool    `json:"overwrite"`
	}
	if json.Unmarshal(data, &payload) != nil {
		s.reply(w, 400, map[string]any{"error": "配置内容无法解析。"})
		return
	}
	if s.saved {
		s.reply(w, 409, map[string]any{"error": "配置已保存，请重新打开 Setup 修改。"})
		return
	}
	switch route {
	case "import":
		base, key, err := s.app.CodexCredentials()
		if err != nil {
			s.reply(w, 400, map[string]any{"error": err.Error()})
			return
		}
		s.cfg.BaseURL, s.cfg.APIKey, s.keyAddress = base, key, base
		s.reply(w, 200, map[string]any{"base_url": base, "api_key_configured": true})
	case "close":
		s.reply(w, 200, map[string]any{"closed": true})
		s.stopAfter(500 * time.Millisecond)
	case "save":
		if s.original != "" && !payload.Overwrite {
			s.reply(w, 409, map[string]any{"error": "请确认替换已有的生图配置。"})
			return
		}
		current, err := fingerprint(s.app.ConfigPath())
		if err != nil || current != s.original {
			s.reply(w, 409, map[string]any{"error": "配置已被其他程序修改，请重新打开 Setup。"})
			return
		}
		base, err := normalizeBase(payload.BaseURL)
		if err != nil {
			s.reply(w, 400, map[string]any{"error": err.Error()})
			return
		}
		cfg := s.cfg
		cfg.BaseURL = base
		if strings.TrimSpace(payload.APIKey) == "" {
			if base != s.keyAddress {
				s.reply(w, 400, map[string]any{"error": "API 地址已改变，请填写该服务的 Key 或重新从 Codex 导入。"})
				return
			}
		} else {
			cfg.APIKey = payload.APIKey
		}
		if payload.Model != nil {
			cfg.Model = *payload.Model
		}
		if payload.Size != nil {
			cfg.Size = *payload.Size
		}
		if payload.Timeout != nil {
			cfg.Timeout = *payload.Timeout
		}
		if payload.OutputDir != nil {
			cfg.OutputDir = *payload.OutputDir
			if cfg.OutputDir == "" {
				cfg.OutputDir = s.app.DefaultOutput()
			}
		}
		if err = s.app.Save(cfg, s.original != ""); err != nil {
			s.reply(w, 400, map[string]any{"error": err.Error()})
			return
		}
		s.saved = true
		s.reply(w, 200, map[string]any{"saved": true, "message": "配置已保存。回到聊天即可开始生成图片。", "environment_overrides": s.public()["environment_overrides"]})
		s.stopAfter(time.Second)
	default:
		s.reply(w, 404, map[string]any{"error": "操作不存在。"})
	}
}

func (a *App) Setup(open bool, timeout int) error {
	if timeout < 10 || timeout > 3600 {
		return fail("invalid_argument", "配置页面有效期必须在 10–3600 秒之间。")
	}
	session, err := a.newSetup(time.Duration(timeout) * time.Second)
	if err != nil {
		return err
	}
	defer session.Close()
	address := session.origin + session.prefix
	encode(a.Out, map[string]any{"setup_url": address, "expires_in_seconds": timeout, "runtime": "native"})
	if open {
		var command *exec.Cmd
		switch runtime.GOOS {
		case "darwin":
			command = exec.Command("open", address)
		case "windows":
			command = exec.Command("rundll32.exe", "url.dll,FileProtocolHandler", address)
		default:
			command = exec.Command("xdg-open", address)
		}
		if err := command.Run(); err != nil {
			_, _ = io.WriteString(a.Err, "浏览器未自动打开，请使用输出的 setup_url。\n")
		}
	}
	<-session.done
	return nil
}
