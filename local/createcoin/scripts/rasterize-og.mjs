/**
 * Rasterize public/logo.svg → public/og.png for Open Graph (WhatsApp expects PNG/JPEG).
 * SVG is rendered with resvg (Corel SVG); padded to 1200×630 with sharp.
 */
import { Resvg } from '@resvg/resvg-js';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, '..');
const svgPath = join(root, 'public', 'logo.svg');
const outPath = join(root, 'public', 'og.png');

/** CorelDRAW often saves SVG as UTF-16; resvg requires UTF-8. */
function readSvgAsUtf8(path) {
  const buf = readFileSync(path);
  let text;
  if (buf.length >= 2 && buf[0] === 0xff && buf[1] === 0xfe) {
    text = buf.subarray(2).toString('utf16le');
  } else if (buf.length >= 2 && buf[0] === 0xfe && buf[1] === 0xff) {
    text = buf.swap16().subarray(2).toString('utf16le');
  } else {
    text = buf.toString('utf8');
  }
  return text.replace(/encoding\s*=\s*["']UTF-16["']/i, 'encoding="UTF-8"');
}

const svg = Buffer.from(readSvgAsUtf8(svgPath), 'utf8');
const resvg = new Resvg(svg, {
  fitTo: { mode: 'width', value: 1600 },
});
const png = resvg.render().asPng();

await sharp(png)
  .resize(1200, 630, {
    fit: 'contain',
    background: { r: 24, g: 63, b: 58, alpha: 1 },
  })
  .png()
  .toFile(outPath);

console.log('og.png written from logo.svg →', outPath);
