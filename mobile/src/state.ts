/**
 * Global app state: the active gateway client + connection config.
 *
 * Kept as a tiny module (instead of scattered globals) so pages can grab the
 * current client without circular imports. `client` is null until the user has
 * saved a gateway configuration; every page must handle that case.
 */

import { GatewayClient, normalizeBaseUrl } from './gateway'
import { getItem, setItem, StorageKeys } from './store'
import type { GatewayConfig } from './types'

let current: GatewayClient | null = null
let currentConfig: GatewayConfig | null = null

export function getClient(): GatewayClient | null {
  return current
}

export function getConfig(): GatewayConfig | null {
  return currentConfig
}

export function hasGateway(): boolean {
  return current !== null
}

/** Build + store the client from an explicit config (used after setup saves). */
export function applyConfig(config: GatewayConfig): void {
  currentConfig = config
  current = new GatewayClient(config)
}

/** Load persisted gateway config; returns true when one existed. */
export async function loadPersistedConfig(): Promise<boolean> {
  const baseUrl = await getItem(StorageKeys.gatewayUrl)
  const apiKey = (await getItem(StorageKeys.gatewayApiKey)) ?? ''
  if (!baseUrl) {
    current = null
    currentConfig = null
    return false
  }
  let normalized: string
  try {
    normalized = normalizeBaseUrl(baseUrl)
  } catch {
    current = null
    currentConfig = null
    return false
  }
  applyConfig({ baseUrl: normalized, apiKey })
  return true
}

export async function persistConfig(config: GatewayConfig): Promise<void> {
  await setItem(StorageKeys.gatewayUrl, config.baseUrl)
  await setItem(StorageKeys.gatewayApiKey, config.apiKey)
  applyConfig(config)
}

export async function clearPersistedConfig(): Promise<void> {
  await setItem(StorageKeys.gatewayUrl, '')
  await setItem(StorageKeys.gatewayApiKey, '')
  current = null
  currentConfig = null
}
