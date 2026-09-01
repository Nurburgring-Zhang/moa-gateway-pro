#!/usr/bin/env node
/**
 * generate-icons.js — rasterize the MOA Gateway brand mark into the legacy
 * launcher PNGs (mipmap-*\/ic_launcher.png + ic_launcher_round.png).
 *
 * Why this exists: API 26+ devices use the adaptive vector icon
 * (res/drawable/ic_launcher_foreground.xml). Devices on minSdk..25 fall back
 * to these raster PNGs, so they must carry the same brand, not the Capacitor
 * template logo. This script is a dependency-free PNG encoder + anti-aliased
 * rasterizer (no ImageMagick / canvas needed), so the icons are reproducible:
 *
 *     node scripts/generate-icons.js
 *
 * The motif matches the vector exactly: three "agent" nodes feeding one
 * "aggregator" node (Mixture-of-Agents), over a dark navy rounded background.
 */
'use strict'

const fs = require('fs')
const path = require('path')
const zlib = require('zlib')

// ---------- PNG encoding (no external deps) ----------

let crcTable = null
function crc32 (buf) {
  if (!crcTable) {
    crcTable = new Array(256)
    for (let n = 0; n < 256; n++) {
      let c = n
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
      crcTable[n] = c >>> 0
    }
  }
  let crc = 0xffffffff
  for (let i = 0; i < buf.length; i++) crc = crcTable[(crc ^ buf[i]) & 0xff] ^ (crc >>> 8)
  return (crc ^ 0xffffffff) >>> 0
}

function chunk (type, data) {
  const len = Buffer.alloc(4)
  len.writeUInt32BE(data.length, 0)
  const typeBuf = Buffer.from(type, 'ascii')
  const crcBuf = Buffer.alloc(4)
  crcBuf.writeUInt32BE(crc32(Buffer.concat([typeBuf, data])), 0)
  return Buffer.concat([len, typeBuf, data, crcBuf])
}

function encodePNG (width, height, rgba) {
  const sig = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(width, 0)
  ihdr.writeUInt32BE(height, 4)
  ihdr[8] = 8 // bit depth
  ihdr[9] = 6 // color type RGBA
  ihdr[10] = 0
  ihdr[11] = 0
  ihdr[12] = 0
  const stride = width * 4
  const raw = Buffer.alloc((stride + 1) * height)
  for (let y = 0; y < height; y++) {
    raw[y * (stride + 1)] = 0 // filter: none
    rgba.copy(raw, y * (stride + 1) + 1, y * stride, (y + 1) * stride)
  }
  const idat = zlib.deflateSync(raw, { level: 9 })
  return Buffer.concat([sig, chunk('IHDR', ihdr), chunk('IDAT', idat), chunk('IEND', Buffer.alloc(0))])
}

// ---------- color + geometry helpers ----------

function clamp01 (v) {
  return v < 0 ? 0 : v > 1 ? 1 : v
}
function lerp (a, b, t) {
  return a + (b - a) * t
}
function hexToRgb (hex) {
  const h = hex.replace('#', '')
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)]
}

const BG_TOP = hexToRgb('#16213A')
const BG_BOTTOM = hexToRgb('#0B1220')
const GRAD_A = hexToRgb('#818CF8')
const GRAD_B = hexToRgb('#22D3EE')

function brandColor (t) {
  t = clamp01(t)
  return [lerp(GRAD_A[0], GRAD_B[0], t), lerp(GRAD_A[1], GRAD_B[1], t), lerp(GRAD_A[2], GRAD_B[2], t)]
}
function bgColor (y01) {
  return [
    lerp(BG_TOP[0], BG_BOTTOM[0], y01),
    lerp(BG_TOP[1], BG_BOTTOM[1], y01),
    lerp(BG_TOP[2], BG_BOTTOM[2], y01)
  ]
}

// Smooth coverage from a signed distance (1px anti-alias band).
function covFromDist (d) {
  return clamp01(0.5 - d)
}
function circleDist (px, py, cx, cy, r) {
  return Math.hypot(px - cx, py - cy) - r
}
function segDist (px, py, x1, y1, x2, y2) {
  const dx = x2 - x1
  const dy = y2 - y1
  const l2 = dx * dx + dy * dy
  let t = l2 === 0 ? 0 : ((px - x1) * dx + (py - y1) * dy) / l2
  t = clamp01(t)
  const cx = x1 + t * dx
  const cy = y1 + t * dy
  return Math.hypot(px - cx, py - cy)
}
function roundedRectDist (px, py, half, r) {
  const qx = Math.abs(px) - (half - r)
  const qy = Math.abs(py) - (half - r)
  const ox = Math.max(qx, 0)
  const oy = Math.max(qy, 0)
  return Math.hypot(ox, oy) + Math.min(Math.max(qx, qy), 0) - r
}

// ---------- the motif (matches the vector, normalized 0..1 from a 200 viewport) ----------

const NODES = [
  { x: 0.325, y: 0.35, r: 0.06 }, // top-left agent
  { x: 0.5, y: 0.28, r: 0.06 }, // top-center agent
  { x: 0.675, y: 0.35, r: 0.06 }, // top-right agent
  { x: 0.5, y: 0.665, r: 0.09 } // aggregator
]
const LINKS = [
  [0.325, 0.35, 0.5, 0.665],
  [0.5, 0.28, 0.5, 0.665],
  [0.675, 0.35, 0.5, 0.665]
]
const LINK_W = 0.016 // half-width of link stroke in motif space

// Center/scale the motif inside the icon.
const mMinX = 0.265
const mMaxX = 0.735
const mMinY = 0.22
const mMaxY = 0.755
const mCX = (mMinX + mMaxX) / 2
const mCY = (mMinY + mMaxY) / 2
const mH = mMaxY - mMinY
const TARGET_H = 0.6 // motif occupies 60% of icon height
const S = TARGET_H / mH

function motifToIconX (mx) {
  return 0.5 + (mx - mCX) * S
}
function motifToIconY (my) {
  return 0.5 + (my - mCY) * S
}

// ---------- rasterizer ----------

function renderIcon (size, shape) {
  const rgba = Buffer.alloc(size * size * 4)
  const SS = 3 // 3x3 supersampling
  const cornerR = 0.2 // rounded-rect corner radius as fraction of size
  const half = 0.5

  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let rA = 0
      let gA = 0
      let bA = 0
      let aA = 0
      for (let sy = 0; sy < SS; sy++) {
        for (let sx = 0; sx < SS; sx++) {
          const u = (x + (sx + 0.5) / SS) / size // 0..1
          const v = (y + (sy + 0.5) / SS) / size

          // Icon mask (rounded square or circle), centered at 0.5,0.5.
          let maskCov
          if (shape === 'circle') {
            maskCov = covFromDist(Math.hypot(u - 0.5, v - 0.5) - 0.48)
          } else {
            maskCov = covFromDist(
              roundedRectDist(u - 0.5, v - 0.5, half, cornerR)
            )
          }
          if (maskCov <= 0) continue

          // Base = background gradient.
          let col = bgColor(v)

          // Brand gradient parameter across the motif (diagonal).
          const tGrad = clamp01((u + v - 0.4) / 0.7)

          // Links (under nodes).
          let linkCov = 0
          for (const [x1, y1, x2, y2] of LINKS) {
            const d = segDist(
              u,
              v,
              motifToIconX(x1),
              motifToIconY(y1),
              motifToIconX(x2),
              motifToIconY(y2)
            )
            linkCov = Math.max(linkCov, covFromDist(d - LINK_W * S))
          }
          if (linkCov > 0) {
            const lc = brandColor(tGrad)
            col = [
              lerp(col[0], lc[0], linkCov),
              lerp(col[1], lc[1], linkCov),
              lerp(col[2], lc[2], linkCov)
            ]
          }

          // Nodes (over links).
          for (const n of NODES) {
            const d = circleDist(u, v, motifToIconX(n.x), motifToIconY(n.y), n.r * S)
            const nc = covFromDist(d)
            if (nc > 0) {
              const c = brandColor(clamp01((n.x + n.y - 0.4) / 0.7))
              col = [lerp(col[0], c[0], nc), lerp(col[1], c[1], nc), lerp(col[2], c[2], nc)]
            }
          }

          rA += col[0] * maskCov
          gA += col[1] * maskCov
          bA += col[2] * maskCov
          aA += maskCov
        }
      }
      const samples = SS * SS
      const idx = (y * size + x) * 4
      if (aA > 0) {
        rgba[idx] = Math.round(rA / aA)
        rgba[idx + 1] = Math.round(gA / aA)
        rgba[idx + 2] = Math.round(bA / aA)
        rgba[idx + 3] = Math.round((aA / samples) * 255)
      } else {
        rgba[idx + 3] = 0
      }
    }
  }
  return encodePNG(size, size, rgba)
}

// ---------- emit ----------

const DENSITIES = {
  mdpi: 48,
  hdpi: 72,
  xhdpi: 96,
  xxhdpi: 144,
  xxxhdpi: 192
}

const resDir = path.join(__dirname, '..', 'android', 'app', 'src', 'main', 'res')

for (const [density, size] of Object.entries(DENSITIES)) {
  const dir = path.join(resDir, `mipmap-${density}`)
  fs.mkdirSync(dir, { recursive: true })
  const square = renderIcon(size, 'square')
  const round = renderIcon(size, 'circle')
  fs.writeFileSync(path.join(dir, 'ic_launcher.png'), square)
  fs.writeFileSync(path.join(dir, 'ic_launcher_round.png'), round)
  console.log(`  wrote mipmap-${density}/ic_launcher.png + ic_launcher_round.png (${size}x${size})`)
}

console.log('Done. Legacy launcher PNGs regenerated with the MOA Gateway brand mark.')
