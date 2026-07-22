package main

import (
	"net"
	"net/http"
	"time"
)

// NewOptimizedTransport creates a high-performance HTTP transport with
// connection pooling tuned for reverse proxy workloads.
func NewOptimizedTransport(cfg *Config) *http.Transport {
	return &http.Transport{
		DialContext: (&net.Dialer{
			Timeout:   5 * time.Second,
			KeepAlive: 30 * time.Second,
		}).DialContext,
		MaxIdleConns:          cfg.MaxConnections,
		MaxIdleConnsPerHost:   cfg.MaxConnections,
		MaxConnsPerHost:       cfg.MaxConnections * 2,
		IdleConnTimeout:       90 * time.Second,
		TLSHandshakeTimeout:  5 * time.Second,
		ExpectContinueTimeout: 1 * time.Second,
		ForceAttemptHTTP2:     true,
		DisableCompression:    false,
	}
}
