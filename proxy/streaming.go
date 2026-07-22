package main

import (
	"bufio"
	"fmt"
	"io"
	"net/http"
	"time"
)

// handleStreaming performs zero-buffer SSE stream forwarding from backend.
func (h *ProxyHandler) handleStreaming(w http.ResponseWriter, r *http.Request, start time.Time) {
	// Build backend request
	backendURL := h.backend.String() + r.URL.RequestURI()
	req, err := http.NewRequestWithContext(r.Context(), r.Method, backendURL, r.Body)
	if err != nil {
		http.Error(w, `{"error":"proxy_error"}`, http.StatusBadGateway)
		return
	}

	// Copy all request headers
	for k, vv := range r.Header {
		for _, v := range vv {
			req.Header.Add(k, v)
		}
	}
	req.Header.Set("X-Forwarded-For", extractClientIP(r))
	req.Header.Set("X-Forwarded-Proto", "http")

	resp, err := h.transport.RoundTrip(req)
	if err != nil {
		http.Error(w, `{"error":"backend_unavailable"}`, http.StatusBadGateway)
		h.metrics.RecordRequest(r.URL.Path, 502, time.Since(start))
		return
	}
	defer resp.Body.Close()

	// Set SSE response headers
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	w.Header().Set("X-Proxy", "moa-go")
	w.WriteHeader(resp.StatusCode)

	flusher, ok := w.(http.Flusher)
	if !ok {
		// Fallback: simple copy
		io.Copy(w, resp.Body)
		h.metrics.RecordRequest(r.URL.Path, resp.StatusCode, time.Since(start))
		return
	}

	// Zero-buffer streaming relay
	scanner := bufio.NewScanner(resp.Body)
	scanner.Buffer(make([]byte, 64*1024), 1024*1024) // 64KB buffer, 1MB max line
	for scanner.Scan() {
		line := scanner.Text()
		fmt.Fprintf(w, "%s\n", line)
		flusher.Flush()
	}

	h.metrics.RecordRequest(r.URL.Path, resp.StatusCode, time.Since(start))
}
