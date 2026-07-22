package main

import (
	"fmt"
	"net/http"
	"sync"
	"sync/atomic"
	"time"
)

// MetricsCollector provides lock-free request metrics.
type MetricsCollector struct {
	totalRequests  uint64
	totalLatencyNs uint64
	statusCodes    sync.Map // string(status) -> *uint64
}

// NewMetricsCollector creates a new metrics instance.
func NewMetricsCollector() *MetricsCollector {
	return &MetricsCollector{}
}

// RecordRequest atomically records a request's status and latency.
func (m *MetricsCollector) RecordRequest(path string, status int, latency time.Duration) {
	atomic.AddUint64(&m.totalRequests, 1)
	atomic.AddUint64(&m.totalLatencyNs, uint64(latency.Nanoseconds()))

	key := fmt.Sprintf("%d", status)
	if val, ok := m.statusCodes.Load(key); ok {
		atomic.AddUint64(val.(*uint64), 1)
	} else {
		var count uint64 = 1
		m.statusCodes.Store(key, &count)
	}
}

// MetricsHandler exposes Prometheus-compatible metrics.
func (h *ProxyHandler) MetricsHandler(w http.ResponseWriter, r *http.Request) {
	total := atomic.LoadUint64(&h.metrics.totalRequests)
	totalLatency := atomic.LoadUint64(&h.metrics.totalLatencyNs)
	avgLatency := float64(0)
	if total > 0 {
		avgLatency = float64(totalLatency) / float64(total) / 1e6 // ms
	}

	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	fmt.Fprintf(w, "# HELP moa_proxy_requests_total Total number of proxied requests\n")
	fmt.Fprintf(w, "# TYPE moa_proxy_requests_total counter\n")
	fmt.Fprintf(w, "moa_proxy_requests_total %d\n\n", total)

	fmt.Fprintf(w, "# HELP moa_proxy_latency_avg_ms Average request latency in milliseconds\n")
	fmt.Fprintf(w, "# TYPE moa_proxy_latency_avg_ms gauge\n")
	fmt.Fprintf(w, "moa_proxy_latency_avg_ms %.3f\n\n", avgLatency)

	fmt.Fprintf(w, "# HELP moa_proxy_uptime_seconds Proxy uptime in seconds\n")
	fmt.Fprintf(w, "# TYPE moa_proxy_uptime_seconds gauge\n")
	fmt.Fprintf(w, "moa_proxy_uptime_seconds %.0f\n\n", time.Since(h.startTime).Seconds())

	// Per-status breakdown
	fmt.Fprintf(w, "# HELP moa_proxy_responses_by_status Response count by HTTP status\n")
	fmt.Fprintf(w, "# TYPE moa_proxy_responses_by_status counter\n")
	h.metrics.statusCodes.Range(func(key, value interface{}) bool {
		count := atomic.LoadUint64(value.(*uint64))
		fmt.Fprintf(w, "moa_proxy_responses_by_status{code=\"%s\"} %d\n", key.(string), count)
		return true
	})
}
