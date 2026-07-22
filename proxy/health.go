package main

import (
	"encoding/json"
	"net/http"
	"sync/atomic"
	"time"
)

// HealthCheck reports proxy and backend health status.
func (h *ProxyHandler) HealthCheck(w http.ResponseWriter, r *http.Request) {
	client := &http.Client{Timeout: 2 * time.Second}
	resp, err := client.Get(h.cfg.BackendAddr + "/health")

	status := "healthy"
	backendStatus := "unknown"
	httpCode := http.StatusOK

	if err != nil {
		status = "degraded"
		backendStatus = "unreachable"
		httpCode = http.StatusServiceUnavailable
	} else {
		resp.Body.Close()
		if resp.StatusCode == 200 {
			backendStatus = "healthy"
		} else {
			backendStatus = "unhealthy"
			status = "degraded"
			httpCode = http.StatusServiceUnavailable
		}
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(httpCode)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status":         status,
		"backend":        backendStatus,
		"proxy":          "moa-go-proxy",
		"version":        "1.0.0",
		"uptime_seconds": time.Since(h.startTime).Seconds(),
		"total_requests": atomic.LoadUint64(&h.reqCount),
	})
}
