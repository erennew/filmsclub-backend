// generate-icons.js — run with: node generate-icons.js
// Creates all required PWA icon sizes using HTML Canvas via node-canvas
// If node-canvas isn't available, falls back to writing placeholder SVGs

const fs = require('fs');
const path = require('path');

const ICON_SIZES = [72, 96, 128, 144, 152, 192, 384, 512];
const OUT_DIR = path.join(__dirname, 'public', 'icons');

if (!fs.existsSync(OUT_DIR)) fs.mkdirSync(OUT_DIR, { recursive: true });

function createSVGIcon(size) {
  const pad = Math.round(size * 0.12);
  const r = Math.round(size * 0.22);
  const fontSize = Math.round(size * 0.38);
  return `<?xml version="1.0" encoding="UTF-8"?>
<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0a0a14"/>
      <stop offset="100%" stop-color="#0d1730"/>
    </linearGradient>
    <linearGradient id="accent" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0071EB"/>
      <stop offset="100%" stop-color="#3b82f6"/>
    </linearGradient>
    <clipPath id="clip">
      <rect width="${size}" height="${size}" rx="${r}" ry="${r}"/>
    </clipPath>
  </defs>
  <!-- Background -->
  <rect width="${size}" height="${size}" rx="${r}" ry="${r}" fill="url(#bg)"/>
  <!-- Blue glow circle -->
  <circle cx="${size/2}" cy="${size/2}" r="${size * 0.38}" fill="#0071EB" opacity="0.08"/>
  <!-- Play button icon -->
  <polygon
    points="${size*0.36},${size*0.28} ${size*0.76},${size*0.5} ${size*0.36},${size*0.72}"
    fill="url(#accent)"
    opacity="0.95"
  />
  <!-- FC text - only for larger sizes -->
  ${size >= 128 ? `<text x="${size/2}" y="${size*0.88}" text-anchor="middle" font-family="'Inter', 'Arial', sans-serif" font-size="${Math.round(size*0.13)}" font-weight="700" fill="#ffffff" opacity="0.5" letter-spacing="2">FC</text>` : ''}
</svg>`;
}

// Try to use canvas, fall back to SVG
let useCanvas = false;
try {
  const { createCanvas } = require('canvas');
  useCanvas = true;
} catch (e) {
  console.log('node-canvas not found — generating SVG icons (will work as PNG in modern browsers)');
}

ICON_SIZES.forEach((size) => {
  if (useCanvas) {
    try {
      const { createCanvas } = require('canvas');
      const canvas = createCanvas(size, size);
      const ctx = canvas.getContext('2d');
      
      // Background
      const bg = ctx.createLinearGradient(0, 0, size, size);
      bg.addColorStop(0, '#0a0a14');
      bg.addColorStop(1, '#0d1730');
      
      // Rounded rect
      const r = size * 0.22;
      ctx.beginPath();
      ctx.moveTo(r, 0);
      ctx.lineTo(size - r, 0);
      ctx.quadraticCurveTo(size, 0, size, r);
      ctx.lineTo(size, size - r);
      ctx.quadraticCurveTo(size, size, size - r, size);
      ctx.lineTo(r, size);
      ctx.quadraticCurveTo(0, size, 0, size - r);
      ctx.lineTo(0, r);
      ctx.quadraticCurveTo(0, 0, r, 0);
      ctx.closePath();
      ctx.fillStyle = bg;
      ctx.fill();
      
      // Glow
      ctx.beginPath();
      ctx.arc(size/2, size/2, size * 0.38, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(0, 113, 235, 0.08)';
      ctx.fill();
      
      // Play triangle
      const grad = ctx.createLinearGradient(0, 0, size, size);
      grad.addColorStop(0, '#0071EB');
      grad.addColorStop(1, '#3b82f6');
      ctx.beginPath();
      ctx.moveTo(size * 0.36, size * 0.28);
      ctx.lineTo(size * 0.76, size * 0.5);
      ctx.lineTo(size * 0.36, size * 0.72);
      ctx.closePath();
      ctx.fillStyle = grad;
      ctx.fill();
      
      const buffer = canvas.toBuffer('image/png');
      fs.writeFileSync(path.join(OUT_DIR, `icon-${size}x${size}.png`), buffer);
      console.log(`✓ Generated icon-${size}x${size}.png`);
    } catch(e) {
      console.error(`  Canvas failed for ${size}px:`, e.message);
    }
  } else {
    // Write SVG (rename as .png - browsers will handle it, or rename properly)
    const svgContent = createSVGIcon(size);
    fs.writeFileSync(path.join(OUT_DIR, `icon-${size}x${size}.svg`), svgContent);
    console.log(`✓ Generated icon-${size}x${size}.svg (rename to .png or use SVG source)`);
  }
});

console.log('\nDone! Icons are in:', OUT_DIR);
