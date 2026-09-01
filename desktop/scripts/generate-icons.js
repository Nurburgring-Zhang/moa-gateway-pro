'use strict';

/**
 * generate-icons.js — builds assets/icon.png, assets/tray.png and
 * assets/icon.ico with ZERO external dependencies.
 *
 * Pipeline:
 *   1. Render the icon with per-pixel signed-distance-field math
 *      (rounded-square gradient tile + hexagonal gateway emblem + routing
 *      lines), 2x2 supersampled for anti-aliasing.
 *   2. Encode PNG by hand (IHDR/IDAT/IEND + zlib deflate + CRC32).
 *   3. Pack multi-size PNG-in-ICO (valid on Windows Vista+; electron-builder
 *      and the Windows shell both read PNG-compressed ICO entries).
 *
 * Run: node scripts/generate-icons.js
 */

const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');

// ---------------------------------------------------------------------------
// PNG encoding (hand-rolled, no deps)
// ---------------------------------------------------------------------------

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i += 1) {
    c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  }
  return (c ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length, 0);
  const typeBuf = Buffer.from(type, 'ascii');
  const crcBuf = Buffer.alloc(4);
  crcBuf.writeUInt32BE(crc32(Buffer.concat([typeBuf, data])), 0);
  return Buffer.concat([len, typeBuf, data, crcBuf]);
}

/**
 * Encode an RGBA pixel buffer as PNG.
 * @param {number} width
 * @param {number} height
 * @param {Buffer} rgba width*height*4 bytes
 */
function encodePNG(width, height, rgba) {
  if (rgba.length !== width * height * 4) {
    throw new Error(`rgba buffer size mismatch: ${rgba.length} != ${width * height * 4}`);
  }
  const signature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;  // bit depth
  ihdr[9] = 6;  // color type RGBA
  ihdr[10] = 0; // compression
  ihdr[11] = 0; // filter
  ihdr[12] = 0; // no interlace

  // Scanlines with filter type 0 (None) per row.
  const stride = width * 4;
  const raw = Buffer.alloc((stride + 1) * height);
  for (let y = 0; y < height; y += 1) {
    raw[y * (stride + 1)] = 0;
    rgba.copy(raw, y * (stride + 1) + 1, y * stride, (y + 1) * stride);
  }
  const idat = zlib.deflateSync(raw, { level: 9 });

  return Buffer.concat([
    signature,
    pngChunk('IHDR', ihdr),
    pngChunk('IDAT', idat),
    pngChunk('IEND', Buffer.alloc(0)),
  ]);
}

// ---------------------------------------------------------------------------
// SDF helpers
// ---------------------------------------------------------------------------

function sdRoundBox(px, py, hx, hy, r) {
  const qx = Math.abs(px) - (hx - r);
  const qy = Math.abs(py) - (hy - r);
  const ax = Math.max(qx, 0);
  const ay = Math.max(qy, 0);
  return Math.hypot(ax, ay) + Math.min(Math.max(qx, qy), 0) - r;
}

function sdSegment(px, py, ax, ay, bx, by) {
  const abx = bx - ax;
  const aby = by - ay;
  const apx = px - ax;
  const apy = py - ay;
  const t = Math.max(0, Math.min(1, (apx * abx + apy * aby) / (abx * abx + aby * aby || 1)));
  return Math.hypot(px - (ax + abx * t), py - (ay + aby * t));
}

function sdCircle(px, py, cx, cy, r) {
  return Math.hypot(px - cx, py - cy) - r;
}

/** Regular hexagon SDF (flat top/bottom, pointy left/right), circumradius R. */
function sdHexagon(px, py, R) {
  const ax = Math.abs(px);
  const ay = Math.abs(py);
  // Hexagon with vertices at (±R, 0) and (±R/2, ±R*sqrt(3)/2).
  const s3 = Math.sqrt(3);
  // Distance constrained by three edge half-planes.
  const d1 = ay - (s3 / 2) * R;                 // top/bottom edges
  const d2 = (s3 / 2) * ax + 0.5 * ay - (s3 / 2) * R; // diagonal edges
  return Math.max(d1, d2);
}

function clamp01(v) {
  return Math.max(0, Math.min(1, v));
}

function mix(a, b, t) {
  return a + (b - a) * t;
}

function mixColor(c1, c2, t) {
  return [
    Math.round(mix(c1[0], c2[0], t)),
    Math.round(mix(c1[1], c2[1], t)),
    Math.round(mix(c1[2], c2[2], t)),
  ];
}

// ---------------------------------------------------------------------------
// Icon composition
// ---------------------------------------------------------------------------

const BG_TOP = [24, 36, 82];      // #182452
const BG_BOTTOM = [10, 15, 38];   // #0a0f26
const EDGE = [43, 62, 120];       // subtle tile edge highlight
const CYAN = [69, 214, 255];      // #45d6ff
const VIOLET = [143, 107, 255];   // #8f6bff
const LINE_COL = [111, 183, 255]; // #6fb7ff
const DOT_COL = [160, 220, 255];

// Geometry in normalized units u ∈ [-1, 1] across the icon.
const HEX_R = 0.34;
const STROKE = 0.052;
const LINE_W = 0.030;
const LINES = [
  // left side (converging into the gateway)
  { ax: -0.80, ay: -0.34, bx: -0.38, by: -0.19 },
  { ax: -0.82, ay: 0.0, bx: -0.40, by: 0.0 },
  { ax: -0.80, ay: 0.34, bx: -0.38, by: 0.19 },
  // right side (diverging out)
  { ax: 0.38, ay: -0.19, bx: 0.80, by: -0.34 },
  { ax: 0.40, ay: 0.0, bx: 0.82, by: 0.0 },
  { ax: 0.38, ay: 0.19, bx: 0.80, by: 0.34 },
];
const OUTER_DOTS = [
  [-0.80, -0.34], [-0.82, 0.0], [-0.80, 0.34],
  [0.80, -0.34], [0.82, 0.0], [0.80, 0.34],
];

/**
 * Render the icon at `size` pixels with 2x2 supersampling.
 * @returns {Buffer} RGBA buffer
 */
function renderIcon(size) {
  const rgba = Buffer.alloc(size * size * 4);
  const SS = 2; // supersampling factor
  const inv = 2 / size;

  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      let r = 0;
      let g = 0;
      let b = 0;
      let a = 0;
      for (let sy = 0; sy < SS; sy += 1) {
        for (let sx = 0; sx < SS; sx += 1) {
          const u = ((x + (sx + 0.5) / SS) * inv) - 1;
          const v = ((y + (sy + 0.5) / SS) * inv) - 1;
          const [sr, sg, sb, sa] = samplePixel(u, v, size);
          r += sr * sa;
          g += sg * sa;
          b += sb * sa;
          a += sa;
        }
      }
      const n = SS * SS;
      const idx = (y * size + x) * 4;
      if (a > 0) {
        rgba[idx] = Math.round(r / a);
        rgba[idx + 1] = Math.round(g / a);
        rgba[idx + 2] = Math.round(b / a);
      }
      rgba[idx + 3] = Math.round((a / n) * 255);
    }
  }
  return rgba;
}

/** Sample one normalized point; returns [r, g, b, alpha(0..1)]. */
function samplePixel(u, v, sizePx) {
  const px = 2 / sizePx; // size of one pixel in normalized units

  // --- rounded-square tile -------------------------------------------------
  const dTile = sdRoundBox(u, v, 0.94, 0.94, 0.24);
  const tileAlpha = clamp01(0.5 - dTile / px);
  if (tileAlpha <= 0) return [0, 0, 0, 0];

  // Vertical gradient background + faint radial lift in the center.
  const tGrad = clamp01((v + 1) / 2);
  let bg = mixColor(BG_TOP, BG_BOTTOM, tGrad);
  const radial = clamp01(1 - Math.hypot(u, v * 1.15) / 1.25);
  bg = mixColor(bg, [34, 52, 110], radial * 0.35);

  // Subtle inner edge highlight.
  const edgeDist = Math.abs(dTile);
  if (edgeDist < px * 2.2) {
    bg = mixColor(bg, EDGE, clamp01(1 - edgeDist / (px * 2.2)) * 0.5);
  }

  let color = bg;
  let alpha = tileAlpha;

  const blend = (fg, fgAlpha) => {
    const fa = clamp01(fgAlpha);
    if (fa <= 0) return;
    color = mixColor(color, fg, fa);
    alpha = Math.max(alpha, fa * tileAlpha);
  };

  // --- routing lines -------------------------------------------------------
  for (const seg of LINES) {
    const d = sdSegment(u, v, seg.ax, seg.ay, seg.bx, seg.by) - LINE_W;
    blend(LINE_COL, clamp01(0.5 - d / px));
    // soft glow
    blend(LINE_COL, clamp01(1 - Math.max(0, d) / (px * 6)) * 0.12);
  }

  // --- endpoint dots ---------------------------------------------------------
  for (const [cx, cy] of OUTER_DOTS) {
    const d = sdCircle(u, v, cx, cy, 0.048);
    blend(DOT_COL, clamp01(0.5 - d / px));
    blend(DOT_COL, clamp01(1 - Math.max(0, d) / (px * 5)) * 0.18);
  }

  // --- hexagonal gateway outline --------------------------------------------
  const dHex = sdHexagon(u, v, HEX_R);
  const dOutline = Math.abs(dHex) - STROKE / 2;
  const hexAlpha = clamp01(0.5 - dOutline / px);
  const tHex = clamp01((v + HEX_R) / (2 * HEX_R));
  const hexColor = mixColor(CYAN, VIOLET, tHex);
  blend(hexColor, hexAlpha);
  // glow around the outline
  blend(hexColor, clamp01(1 - Math.abs(dHex) / (px * 9)) * 0.22);

  // --- center node -----------------------------------------------------------
  const dCore = sdCircle(u, v, 0, 0, 0.088);
  const coreColor = mixColor(CYAN, [230, 245, 255], 0.45);
  blend(coreColor, clamp01(0.5 - dCore / px));
  blend(CYAN, clamp01(1 - Math.max(0, dCore) / (px * 8)) * 0.3);

  return [color[0], color[1], color[2], alpha];
}

// ---------------------------------------------------------------------------
// ICO packing (PNG-compressed entries, valid Vista+)
// ---------------------------------------------------------------------------

function packIco(pngsBySize) {
  const sizes = Object.keys(pngsBySize).map(Number).sort((a, b) => b - a);
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0);      // reserved
  header.writeUInt16LE(1, 2);      // type: icon
  header.writeUInt16LE(sizes.length, 4);

  const entries = [];
  let offset = 6 + sizes.length * 16;
  for (const size of sizes) {
    const png = pngsBySize[size];
    const entry = Buffer.alloc(16);
    entry[0] = size >= 256 ? 0 : size; // width (0 means 256)
    entry[1] = size >= 256 ? 0 : size; // height
    entry[2] = 0;                      // palette
    entry[3] = 0;                      // reserved
    entry.writeUInt16LE(1, 4);         // planes
    entry.writeUInt16LE(32, 6);        // bpp
    entry.writeUInt32LE(png.length, 8);
    entry.writeUInt32LE(offset, 12);
    entries.push(entry);
    offset += png.length;
  }
  return Buffer.concat([header, ...entries, ...sizes.map((s) => pngsBySize[s])]);
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

function main() {
  const assetsDir = path.resolve(__dirname, '..', 'assets');
  fs.mkdirSync(assetsDir, { recursive: true });

  const png512 = encodePNG(512, 512, renderIcon(512));
  fs.writeFileSync(path.join(assetsDir, 'icon.png'), png512);
  console.log(`[icons] wrote assets/icon.png (${png512.length} bytes, 512x512)`);

  const png32 = encodePNG(32, 32, renderIcon(32));
  fs.writeFileSync(path.join(assetsDir, 'tray.png'), png32);
  console.log(`[icons] wrote assets/tray.png (${png32.length} bytes, 32x32)`);

  const icoSizes = [256, 48, 32, 16];
  const pngs = {};
  for (const s of icoSizes) {
    pngs[s] = encodePNG(s, s, renderIcon(s));
  }
  const ico = packIco(pngs);
  fs.writeFileSync(path.join(assetsDir, 'icon.ico'), ico);
  console.log(`[icons] wrote assets/icon.ico (${ico.length} bytes, sizes: ${icoSizes.join('/')})`);
}

if (require.main === module) {
  main();
}

module.exports = { encodePNG, renderIcon, packIco, crc32 };
