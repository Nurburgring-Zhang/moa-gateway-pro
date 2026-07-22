package main

import (
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
	"sync/atomic"
	"time"
)

// ProxyHandler is the core reverse proxy implementation.
type ProxyHandler struct {
	cfg       *Config
	backend   *url.URL
	proxy     *httputil.ReverseProxy
	transport *http.Transport
	metrics   *MetricsCollector
	reqCount  uint64
	startTime time.Time
}

// NewProxyHandler creates a new proxy with optimised transport.
func NewProxyHandler(cfg *Config) *ProxyHandler {
	backend, _ := url.Parse(cfg.BackendAddr)

	transport := &http.Transport{
		MaxIdleConns:        cfg.MaxConnections,
		MaxIdleConnsPerHost: cfg.MaxConnections,
		MaxConnsPerHost:     cfg.MaxConnections,
		IdleConnTimeout:     90 * time.Second,
		DisableCompression:  false,
		ForceAttemptHTTP2:   true,
	}

	proxy := httputil.NewSingleHostReverseProxy(backend)
	proxy.Transport = transport
	proxy.FlushInterval = -1 // Streaming: flush immediately

	return &ProxyHandler{
		cfg:       cfg,
		backend:   backend,
		proxy:     proxy,
		transport: transport,
		metrics:   NewMetricsCollector(),
		startTime: time.Now(),
	}
}

// ServeHTTP routes requests through auth, streaming detection, then proxy.
func (h *ProxyHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	atomic.AddUint64(&h.reqCount, 1)

	// JWT快速验证（如果启用）
	if h.cfg.EnableAuth && requiresAuth(r.URL.Path) {
		if !h.quickAuthCheck(r) {
			http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
			h.metrics.RecordRequest(r.URL.Path, 401, time.Since(start))
			return
		}
	}

	// SSE流检测
	if isStreamingRequest(r) {
		h.handleStreaming(w, r, start)
		return
	}

	// 标准反向代理
	h.proxy.ServeHTTP(w, r)
	h.metrics.RecordRequest(r.URL.Path, 200, time.Since(start))
}

func requiresAuth(path string) bool {
	public := []string{"/health", "/metrics", "/v1/models", "/openapi.json", "/docs"}
	for _, p := range public {
		if strings.HasPrefix(path, p) {
			return false
		}
	}
	return true
}

func isStreamingRequest(r *http.Request) bool {
	if r.Header.Get("Accept") == "text/event-stream" {
		return true
	}
	if strings.Contains(r.URL.Path, "/chat/completions") {
		// Check if stream=true in body (heuristic via header)
		if r.Header.Get("X-Stream") == "true" {
			return true
		}
	}
	return strings.Contains(r.URL.Path, "/sse")
}
