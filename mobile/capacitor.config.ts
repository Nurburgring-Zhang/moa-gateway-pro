import type { CapacitorConfig } from '@capacitor/cli';

/**
 * MoA Gateway Pro — Android client (Capacitor).
 *
 * Architecture (honest): Python does not run on-device, so the Android app is a
 * CLIENT that connects to a DEPLOYED MoA gateway (self-hosted server). Two modes:
 *
 *   A) Remote gateway (recommended): set `server.url` to the gateway's reachable
 *      address (e.g. http://192.168.1.10:8910 or https://moa.example.com). The
 *      admin/orchestration web UI is served by the gateway and wrapped natively.
 *
 *   B) Bundled static UI: build admin-ui as a static export into `dist/` and set
 *      `webDir: 'dist'`; the UI then calls the gateway API over the network.
 *
 * Building the .apk requires the Android SDK + gradle on a proper machine:
 *   npm install && npx cap add android && npm run build:apk
 * It is NOT produced inside the audit sandbox (no Android SDK; containers forbidden).
 */
const config: CapacitorConfig = {
  appId: 'com.moagateway.mobile',
  appName: 'MoA Gateway Pro',
  // Mode A: connect to a deployed gateway (set your gateway address):
  server: {
    url: 'http://192.168.1.10:8910',
    cleartext: true, // allow http on LAN; use https in production
    androidScheme: 'https',
  },
  // Mode B (alternative): bundled static UI
  // webDir: 'dist',
  plugins: {
    SplashScreen: { launchShowDuration: 800 },
  },
};

export default config;
