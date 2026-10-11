// Copyright (C) 2026 luckygopher
// SPDX-License-Identifier: GPL-3.0-only
package imagegen

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime/multipart"
	"net"
	"net/http"
	"net/textproto"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unicode/utf8"
)

const maxImage int64 = 32 * 1024 * 1024
const maxUpload int64 = 64 * 1024 * 1024
const maxResponse int64 = 128 * 1024 * 1024

func randomToken(size int) string {
	data := make([]byte, size)
	if _, err := rand.Read(data); err != nil {
		panic("secure random source unavailable")
	}
	return hex.EncodeToString(data)
}

func networkError(err error) error {
	var dns *net.DNSError
	var authority x509.UnknownAuthorityError
	var hostname x509.HostnameError
	var certificate *tls.CertificateVerificationError
	var timeout net.Error
	switch {
	case errors.Is(err, syscall.EPERM), errors.Is(err, syscall.EACCES):
		return fail("network_permission_denied", "当前执行环境不允许联网，请通过执行器申请联网权限。未自动重试生图。")
	case errors.As(err, &authority), errors.As(err, &hostname), errors.As(err, &certificate):
		return fail("tls_certificate_error", "HTTPS 证书校验失败，请检查系统时间、代理或系统信任设置。未自动重试。")
	case errors.As(err, &dns):
		return fail("dns_error", "无法解析服务域名。执行环境可能限制联网，请先给 doctor 连接检查申请联网权限。未自动重试生图。")
	case errors.As(err, &timeout) && timeout.Timeout(), errors.Is(err, context.DeadlineExceeded):
		return fail("timeout", "请求超时，服务可能已受理或计费。未自动重试生图。")
	case errors.Is(err, syscall.ECONNREFUSED):
		return fail("connection_refused", "服务拒绝连接，请检查服务地址和网络代理。未自动重试。")
	}
	var known *Failure
	if errors.As(err, &known) {
		return known
	}
	return fail("connection_error", "无法连接图片服务，请用 doctor 检查网络。未自动重试生图。")
}

func validDownload(value string) error {
	u, err := url.Parse(value)
	if err != nil || u.Hostname() == "" || u.User != nil {
		return fail("invalid_response", "图片下载地址无效。")
	}
	local := u.Hostname() == "localhost" || u.Hostname() == "127.0.0.1" || u.Hostname() == "::1"
	if u.Scheme != "https" && !(u.Scheme == "http" && local) {
		return fail("invalid_response", "图片下载必须使用 HTTPS 或本机 HTTP 地址。")
	}
	return nil
}

// Explicit CA environment settings are optional; normal requests use OS trust.
func (a *App) explicitRoots() (*x509.CertPool, error) {
	file, directory := a.Getenv("SSL_CERT_FILE"), a.Getenv("SSL_CERT_DIR")
	if file == "" && directory == "" {
		return nil, nil
	}
	pool := x509.NewCertPool()
	loaded := false
	if file != "" {
		data, err := os.ReadFile(file)
		if err != nil {
			return nil, fail("tls_configuration_error", "无法读取 SSL_CERT_FILE 指定的可信 CA 文件。")
		}
		loaded = pool.AppendCertsFromPEM(data)
	}
	if directory != "" {
		entries, err := os.ReadDir(directory)
		if err != nil {
			return nil, fail("tls_configuration_error", "无法读取 SSL_CERT_DIR 指定的可信 CA 目录。")
		}
		for _, entry := range entries {
			if !entry.IsDir() {
				data, err := os.ReadFile(filepath.Join(directory, entry.Name()))
				if err == nil && pool.AppendCertsFromPEM(data) {
					loaded = true
				}
			}
		}
	}
	if !loaded {
		return nil, fail("tls_configuration_error", "指定的可信 CA 配置中没有有效证书。")
	}
	return pool, nil
}

func (a *App) request(request *http.Request, authenticated bool) (*http.Response, func(), error) {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.TLSClientConfig = &tls.Config{MinVersion: tls.VersionTLS12}
	configuredTLS := false
	configureTLS := func() error {
		if configuredTLS {
			return nil
		}
		roots, err := a.explicitRoots()
		if err != nil {
			return err
		}
		transport.TLSClientConfig.RootCAs = roots
		configuredTLS = true
		return nil
	}
	if request.URL.Scheme == "https" {
		if err := configureTLS(); err != nil {
			return nil, transport.CloseIdleConnections, err
		}
	}
	client := &http.Client{Transport: transport, CheckRedirect: func(next *http.Request, previous []*http.Request) error {
		if authenticated {
			return fail("redirect_blocked", "API 返回重定向，请配置最终 API 地址。未向重定向目标发送密钥。")
		}
		if len(previous) >= 5 {
			return fail("redirect_blocked", "图片服务重定向次数过多。")
		}
		if err := validDownload(next.URL.String()); err != nil {
			return err
		}
		if next.URL.Scheme == "https" {
			return configureTLS()
		}
		return nil
	}}
	response, err := client.Do(request)
	if err != nil {
		if response != nil {
			response.Body.Close()
		}
		transport.CloseIdleConnections()
		return nil, func() {}, networkError(err)
	}
	return response, transport.CloseIdleConnections, nil
}

func (a *App) Doctor(timeout int) (any, error) {
	if timeout < 1 || timeout > 60 {
		return nil, fail("invalid_argument", "连接检查超时必须在 1–60 秒之间。")
	}
	cfg, _, err := a.Load(false)
	if err != nil {
		return nil, err
	}
	if cfg.BaseURL == "" {
		return nil, fail("not_configured", "请先配置图片 API 地址。")
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeout)*time.Second)
	defer cancel()
	request, _ := http.NewRequestWithContext(ctx, http.MethodHead, cfg.BaseURL+"/models", nil)
	request.Header.Set("User-Agent", "agent-toolkit-image-gen/"+a.Version)
	response, closeTransport, err := a.request(request, false)
	if err != nil {
		return nil, err
	}
	defer closeTransport()
	defer response.Body.Close()
	return map[string]any{"status": "success", "reachable": true, "http_status": response.StatusCode, "tls_verified": response.TLS != nil && len(response.TLS.VerifiedChains) > 0, "credentials_sent": false, "runtime": "native", "note": "仅检查传输连接，不验证 API Key 或模型权限，不生成图片。"}, nil
}

type imageItem struct {
	Base64  string `json:"b64_json"`
	URL     string `json:"url"`
	Revised string `json:"revised_prompt"`
}
type imageResponse struct {
	Data []imageItem `json:"data"`
}

func (a *App) post(cfg Config, route string, body io.Reader, contentType string) ([]imageItem, error) {
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(cfg.Timeout)*time.Second)
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, cfg.BaseURL+route, body)
	if err != nil {
		return nil, fail("invalid_config", "图片 API 地址无效。")
	}
	request.Header.Set("Authorization", "Bearer "+cfg.APIKey)
	request.Header.Set("Content-Type", contentType)
	request.Header.Set("Accept", "application/json")
	request.Header.Set("User-Agent", "agent-toolkit-image-gen/"+a.Version)
	response, closeTransport, err := a.request(request, true)
	if err != nil {
		return nil, err
	}
	defer closeTransport()
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		data, _ := io.ReadAll(io.LimitReader(response.Body, 8192))
		var parsed struct {
			Error struct {
				Message string `json:"message"`
			} `json:"error"`
		}
		_ = json.Unmarshal(data, &parsed)
		message := strings.ReplaceAll(parsed.Error.Message, cfg.APIKey, "[REDACTED]")
		if len(message) > 800 {
			message = message[:800]
		}
		if message == "" {
			message = "请检查服务地址、模型权限或服务状态。"
		}
		return nil, fail("api_error", fmt.Sprintf("图片 API 返回 HTTP %d：%s", response.StatusCode, message))
	}
	data, err := limitRead(response.Body, maxResponse)
	if err != nil {
		return nil, err
	}
	var parsed imageResponse
	if json.Unmarshal(data, &parsed) != nil || len(parsed.Data) == 0 || len(parsed.Data) > 4 {
		return nil, fail("invalid_response", "图片服务没有返回标准 data 图片列表。")
	}
	return parsed.Data, nil
}

func imageType(data []byte) (string, string, error) {
	if bytes.HasPrefix(data, []byte("\x89PNG\r\n\x1a\n")) {
		return "image/png", ".png", nil
	}
	if bytes.HasPrefix(data, []byte{0xff, 0xd8, 0xff}) {
		return "image/jpeg", ".jpg", nil
	}
	if len(data) >= 12 && string(data[:4]) == "RIFF" && string(data[8:12]) == "WEBP" {
		return "image/webp", ".webp", nil
	}
	return "", "", fail("invalid_image", "文件不是可识别的 PNG、JPEG 或 WebP 图片。")
}

func resolveSize(value, model string) (string, error) {
	switch strings.ToLower(value) {
	case "1k":
		value = "1024x1024"
	case "2k":
		value = "2048x2048"
	case "4k":
		value = "3840x2160"
	}
	if value == "auto" {
		return value, nil
	}
	match := regexp.MustCompile(`^([1-9][0-9]{0,4})x([1-9][0-9]{0,4})$`).FindStringSubmatch(value)
	if match == nil {
		return "", fail("invalid_argument", "尺寸应为 auto、1k、2k、4k 或 WIDTHxHEIGHT。")
	}
	w, _ := strconv.Atoi(match[1])
	h, _ := strconv.Atoi(match[2])
	if model == "gpt-image-2" || strings.HasPrefix(model, "gpt-image-2-") {
		if w > 3840 || h > 3840 || w%16 != 0 || h%16 != 0 || w > 3*h || h > 3*w || w*h < 655360 || w*h > 8294400 {
			return "", fail("invalid_argument", "GPT Image 2 尺寸要求：边长为 16 的倍数，最长边不超过 3840，比例不超过 3:1，像素总数在 655360–8294400 之间。")
		}
	}
	return value, nil
}

func (a *App) readImage(path string, mask bool) ([]byte, string, string, error) {
	file, err := os.Open(a.Expand(path))
	if err != nil {
		return nil, "", "", fail("invalid_image", "找不到指定的本地图片。")
	}
	defer file.Close()
	data, err := limitRead(file, maxImage)
	if err != nil {
		return nil, "", "", err
	}
	mime, suffix, err := imageType(data)
	if err != nil {
		return nil, "", "", err
	}
	if mask && mime != "image/png" {
		return nil, "", "", fail("invalid_image", "蒙版必须是透明 PNG 图片。")
	}
	return data, mime, suffix, nil
}

func (a *App) multipart(fields map[string]any, refs []string, mask string) ([]byte, string, error) {
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	for name, value := range fields {
		if err := writer.WriteField(name, fmt.Sprint(value)); err != nil {
			return nil, "", err
		}
	}
	var first []byte
	total := int64(0)
	write := func(field, name, mime string, data []byte) error {
		header := textproto.MIMEHeader{}
		header.Set("Content-Disposition", fmt.Sprintf(`form-data; name="%s"; filename="%s"`, field, name))
		header.Set("Content-Type", mime)
		part, err := writer.CreatePart(header)
		if err != nil {
			return err
		}
		_, err = part.Write(data)
		return err
	}
	for i, path := range refs {
		data, mime, suffix, err := a.readImage(path, false)
		if err != nil {
			return nil, "", err
		}
		total += int64(len(data))
		if total > maxUpload {
			return nil, "", fail("invalid_image", "参考图和蒙版合计不能超过 64 MiB。")
		}
		if i == 0 {
			first = data
		}
		field := "image"
		if len(refs) > 1 {
			field = "image[]"
		}
		if err = write(field, fmt.Sprintf("reference-%d%s", i+1, suffix), mime, data); err != nil {
			return nil, "", err
		}
	}
	if mask != "" {
		data, mime, _, err := a.readImage(mask, true)
		if err != nil {
			return nil, "", err
		}
		total += int64(len(data))
		if total > maxUpload {
			return nil, "", fail("invalid_image", "参考图和蒙版合计不能超过 64 MiB。")
		}
		if len(first) >= 24 && len(data) >= 24 && bytes.HasPrefix(first, []byte("\x89PNG")) {
			if binary.BigEndian.Uint32(first[16:20]) != binary.BigEndian.Uint32(data[16:20]) || binary.BigEndian.Uint32(first[20:24]) != binary.BigEndian.Uint32(data[20:24]) {
				return nil, "", fail("invalid_image", "蒙版尺寸必须与第一张参考图一致。")
			}
		}
		if err = write("mask", "mask.png", mime, data); err != nil {
			return nil, "", err
		}
	}
	if err := writer.Close(); err != nil {
		return nil, "", err
	}
	return body.Bytes(), writer.FormDataContentType(), nil
}

func (a *App) imageBytes(item imageItem, timeout int) ([]byte, error) {
	if item.Base64 != "" {
		if int64(len(item.Base64)) > ((maxImage+2)/3)*4 {
			return nil, fail("invalid_response", "图片超过 32 MiB。")
		}
		data, err := base64.StdEncoding.Strict().DecodeString(item.Base64)
		if err != nil {
			return nil, fail("invalid_response", "图片 base64 无效。")
		}
		return data, nil
	}
	if item.URL == "" {
		return nil, fail("invalid_response", "图片缺少 b64_json 或 url。")
	}
	if err := validDownload(item.URL); err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeout)*time.Second)
	defer cancel()
	request, _ := http.NewRequestWithContext(ctx, http.MethodGet, item.URL, nil)
	request.Header.Set("User-Agent", "agent-toolkit-image-gen/"+a.Version)
	response, closeTransport, err := a.request(request, false)
	if err != nil {
		return nil, fail("download_error", "图片已生成但下载失败。不会重新发起生图请求。")
	}
	defer closeTransport()
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, fail("download_error", fmt.Sprintf("图片下载返回 HTTP %d。不会重新生成。", response.StatusCode))
	}
	return limitRead(response.Body, maxImage)
}

func (a *App) Generate(options Options) (any, error) {
	cfg, _, err := a.Load(true)
	if err != nil {
		return nil, err
	}
	if options.Model != "" {
		cfg.Model = options.Model
	}
	if options.Size != "" {
		cfg.Size = options.Size
	}
	cfg.Size, err = resolveSize(cfg.Size, cfg.Model)
	if err != nil {
		return nil, err
	}
	prompt := options.Prompt
	if options.PromptFile != "" {
		data, err := os.ReadFile(a.Expand(options.PromptFile))
		if err != nil {
			return nil, fail("invalid_argument", "无法读取提示词文件。")
		}
		prompt = string(data)
	}
	if strings.TrimSpace(prompt) == "" || utf8.RuneCountInString(prompt) > 32000 {
		return nil, fail("invalid_argument", "图片描述不能为空，且不能超过 32000 字符。")
	}
	if options.Count < 1 || options.Count > 4 {
		return nil, fail("invalid_argument", "一次生成数量必须在 1–4 之间。")
	}
	if options.Background == "transparent" && options.Format == "jpeg" {
		return nil, fail("invalid_argument", "透明背景不能使用 JPEG。")
	}
	fields := map[string]any{"model": cfg.Model, "prompt": prompt, "size": cfg.Size, "n": options.Count}
	for key, value := range map[string]string{"quality": options.Quality, "output_format": options.Format, "background": options.Background} {
		if value != "" {
			fields[key] = value
		}
	}
	var body []byte
	route, contentType := "/images/generations", "application/json"
	if options.Command == "edit" {
		if len(options.References) < 1 || len(options.References) > 16 {
			return nil, fail("invalid_argument", "编辑需要 1–16 张本地参考图。")
		}
		body, contentType, err = a.multipart(fields, options.References, options.Mask)
		route = "/images/edits"
	} else {
		body, err = json.Marshal(fields)
	}
	if err != nil {
		return nil, err
	}
	directory := options.OutputDir
	if directory == "" {
		directory = cfg.OutputDir
	}
	directory, err = filepath.Abs(a.Expand(directory))
	if err != nil {
		return nil, fail("output_error", "图片目录无效。")
	}
	if err = os.MkdirAll(directory, 0755); err != nil {
		return nil, fail("output_error", "无法创建图片输出目录。")
	}
	probe, err := os.CreateTemp(directory, ".image-gen-*")
	if err != nil {
		return nil, fail("output_error", "图片输出目录不可写。")
	}
	probe.Close()
	os.Remove(probe.Name())
	fmt.Fprintf(a.Err, "正在请求图片服务，模型 %s，数量 %d…\n", cfg.Model, options.Count)
	items, err := a.post(cfg, route, bytes.NewReader(body), contentType)
	if err != nil {
		return nil, err
	}
	images, problems := []map[string]any{}, []map[string]any{}
	stamp := time.Now().UTC().Format("20060102T150405Z") + "-" + randomToken(4)
	for i, item := range items {
		data, err := a.imageBytes(item, cfg.Timeout)
		mime, suffix := "", ""
		if err == nil {
			mime, suffix, err = imageType(data)
		}
		path := filepath.Join(directory, fmt.Sprintf("image-%s-%d%s", stamp, i+1, suffix))
		if err == nil {
			var file *os.File
			file, err = os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0644)
			if err == nil {
				_, err = file.Write(data)
				closeErr := file.Close()
				if err == nil {
					err = closeErr
				}
				if err != nil {
					os.Remove(path)
				}
			}
		}
		if err != nil {
			message := "图片无法保存。"
			var failure *Failure
			if errors.As(err, &failure) {
				message = failure.Message
			}
			problems = append(problems, map[string]any{"index": i + 1, "message": message})
			continue
		}
		result := map[string]any{"path": path, "mime_type": mime, "bytes": len(data)}
		if item.Revised != "" {
			result["revised_prompt"] = item.Revised
		}
		images = append(images, result)
	}
	if len(items) != options.Count {
		problems = append(problems, map[string]any{"message": fmt.Sprintf("请求 %d 张，服务返回 %d 张。", options.Count, len(items))})
	}
	status := "success"
	if len(problems) > 0 {
		status = "partial"
		if len(images) == 0 {
			status = "error"
		}
	}
	return map[string]any{"status": status, "model": cfg.Model, "size": cfg.Size, "requested_count": options.Count, "images": images, "errors": problems}, nil
}
