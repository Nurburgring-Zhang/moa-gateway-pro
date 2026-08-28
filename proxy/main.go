package main

import (
	"context"
	"flag"
	"log"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"
)

func main() {
	var (
		listenAddr  = flag.String("listen", ":8080", "Proxy listen address")
		backendAddr = flag.String("backend", "http://127.0.0.1:8000", "Python backend URL")
		configFile  = flag.String("config", "proxy.yaml", "Config file path")
	)
	flag.Parse()

	cfg := LoadConfig(*configFile, *listenAddr, *backendAddr)

	proxy := NewProxyHandler(cfg)

	mux := http.NewServeMux()
	mux.HandleFunc("/health", proxy.HealthCheck)
	mux.HandleFunc("/metrics", proxy.MetricsHandler)
	mux.HandleFunc("/", proxy.ServeHTTP)

	srv := &http.Server{
		Addr:         cfg.ListenAddr,
		Handler:      Chain(mux, LoggingMiddleware, RecoveryMiddleware, RateLimitMiddleware(cfg)),
		ReadTimeout:  time.Duration(cfg.ReadTimeout) * time.Second,
		WriteTimeout: time.Duration(cfg.WriteTimeout) * time.Second,
		IdleTimeout:  60 * time.Second,
	}

	// Graceful shutdown
	go func() {
		sigCh := make(chan os.Signal, 1)
		signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
		<-sigCh
		log.Println("Shutting down gracefully...")
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		srv.Shutdown(ctx)
	}()

	// redactURL strips any userinfo (user:pass@host) before logging —
	// backend URLs may embed credentials.
	redactURL := func(raw string) string {
		if u, err := url.Parse(raw); err == nil && u.User != nil {
			u.User = url.User("REDACTED")
			return u.String()
		}
		return raw
	}
	if len(cfg.BackendURLs) > 0 {
		redacted := make([]string, 0, len(cfg.BackendURLs))
		for _, b := range cfg.BackendURLs {
			redacted = append(redacted, redactURL(b))
		}
		log.Printf("MoA Gateway Proxy starting on %s -> %d backends (round-robin): %s",
			cfg.ListenAddr, len(cfg.BackendURLs), strings.Join(redacted, ", "))
	} else {
		log.Printf("MoA Gateway Proxy starting on %s -> %s", cfg.ListenAddr, redactURL(cfg.BackendAddr))
	}
	if err := srv.ListenAndServe(); err != http.ErrServerClosed {
		log.Fatalf("Server error: %v", err)
	}
	log.Println("Server stopped.")
}
