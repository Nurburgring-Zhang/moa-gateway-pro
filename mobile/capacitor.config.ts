import type { CapacitorConfig } from '@capacitor/cli'

/**
 * MOA Gateway mobile console — Capacitor configuration.
 *
 * The gateway is a plain-HTTP FastAPI service on the LAN (default port 8910,
 * see moa_gateway/config.py ServerConfig). Two Android settings below exist
 * specifically because of that:
 *
 * 1. `server.androidScheme: 'https'` — the WebView serves the bundled web app
 *    from the origin `https://localhost`. Keep this origin when adding the app
 *    to the gateway's `server.cors_origins` (see mobile/README.md, section
 *    "Gateway CORS configuration").
 *
 * 2. `android.allowMixedContent: true` — without it the WebView refuses to
 *    issue `http://` requests from the `https://localhost` app origin, which
 *    would make every gateway call fail on a LAN deployment without TLS.
 */
const config: CapacitorConfig = {
  appId: 'com.moagateway.console',
  appName: 'MOA Gateway',
  webDir: 'www',
  server: {
    androidScheme: 'https'
  },
  android: {
    allowMixedContent: true,
    // Enable per WebView debugging in debug builds only is handled natively;
    // keep captureInput default so the keyboard does not swallow touch events
    // meant for the chat composer.
  },
  backgroundColor: '#0B1220'
}

export default config
