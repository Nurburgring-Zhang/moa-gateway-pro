package main

import (
	"context"
	"flag"
	"log"
	"net/http"
	"os"
	"os/signal"
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

	log.Printf("MoA Gateway Proxy starting on %s -> %s", cfg.ListenAddr, cfg.BackendAddr)
	if err := srv.ListenAndServe(); err != http.ErrServerClosed {
		log.Fatalf("Server error: %v", err)
	}
	log.Println("Server stopped.")
}
