package main

import (
	"log"
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
	backends  []*url.URL // pool from BACKEND_URLS (falls back to [backend])
	next      uint64     // atomic round-robin counter
	proxy     *httputil.ReverseProxy
	transport *http.Transport
	metrics   *MetricsCollector
	reqCount  uint64
	startTime time.Time
}

// NewProxyHandler creates a new proxy with optimised transport.
func NewProxyHandler(cfg *Config) *ProxyHandler {
	backend, _ := url.Parse(cfg.BackendAddr)

	// v3.2.1: backend pool. With no BACKEND_URLS the pool holds exactly one
	// target and behaviour is identical to the single-backend proxy.
	backends := make([]*url.URL, 0, len(cfg.BackendURLs)+1)
	for _, raw := range cfg.BackendURLs {
		if u, err := url.Parse(strings.TrimSpace(raw)); err == nil && u.Host != "" {
			backends = append(backends, u)
		} else {
			log.Printf("ignoring invalid BACKEND_URLS entry: %q", raw)
		}
	}
	if len(backends) == 0 {
		backends = []*url.URL{backend}
	}

	transport := &http.Transport{
		MaxIdleConns:        cfg.MaxConnections,
		MaxIdleConnsPerHost: cfg.MaxConnections,
		MaxConnsPerHost:     cfg.MaxConnections,
		IdleConnTimeout:     90 * time.Second,
		DisableCompression:  false,
		ForceAttemptHTTP2:   true,
	}

	h := &ProxyHandler{
		cfg:       cfg,
		backend:   backend,
		backends:  backends,
		transport: transport,
		metrics:   NewMetricsCollector(),
		startTime: time.Now(),
	}

	proxy := &httputil.ReverseProxy{
		Transport:     transport,
		FlushInterval: -1, // Streaming: flush immediately
		Director: func(req *http.Request) {
			target := h.pickBackend()
			req.URL.Scheme = target.Scheme
			req.URL.Host = target.Host
			req.Host = target.Host
			if target.Path != "" && target.Path != "/" {
				req.URL.Path = singleJoiningSlash(target.Path, req.URL.Path)
			}
			// Edge honesty: drop client-supplied XFF so ReverseProxy
			// re-adds only the real connection peer (spoof protection).
			req.Header.Del("X-Forwarded-For")
		},
	}
	h.proxy = proxy

	return h
}

// pickBackend returns the next backend in round-robin order.
func (h *ProxyHandler) pickBackend() *url.URL {
	if len(h.backends) == 1 {
		return h.backends[0]
	}
	i := atomic.AddUint64(&h.next, 1) - 1
	return h.backends[i%uint64(len(h.backends))]
}

// singleJoiningSlash joins a backend base path with the request path.
func singleJoiningSlash(a, b string) string {
	aslash := strings.HasSuffix(a, "/")
	bslash := strings.HasPrefix(b, "/")
	switch {
	case aslash && bslash:
		return a + b[1:]
	case !aslash && !bslash:
		return a + "/" + b
	}
	return a + b
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
