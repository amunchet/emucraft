// Draws a full-coverage engraving design for the 1911 grip sample (47 x 112 mm, 20 px/mm, 2 mm bleed past
// the panel edges) in headless Chromium and saves it as PNG. Black = deepest cut.
// Usage: CHROME=/path/to/chromium node make_1911_bitmap.mjs [out.png]   (needs playwright installed)
import { chromium } from 'playwright';
import fs from 'fs';

const out = process.argv[2] || new URL('./1911_grip_design.png', import.meta.url).pathname;
const b = await chromium.launch({ executablePath: process.env.CHROME, args: ['--no-sandbox'] });
const p = await b.newPage();
const url = await p.evaluate(() => {
  const S = 20, W = 47, H = 112; // px/mm, image size in mm (centred on the panel)
  const c = document.createElement('canvas'); c.width = W * S; c.height = H * S;
  const g = c.getContext('2d');
  g.setTransform(S, 0, 0, -S, (W / 2) * S, (H / 2) * S); // draw in panel mm, +Y up, origin at panel centre
  const text = (s, x, y, size, weight = 'bold', fill = '#000', track = 0) => {
    g.save(); g.translate(x, y); g.scale(1, -1); g.font = `${weight} ${size}px Georgia, serif`;
    g.letterSpacing = `${track}px`; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillStyle = fill; g.fillText(s, 0, 0); g.restore();
  };
  const diamond = (x, y, hw, hh) => { g.beginPath(); g.moveTo(x, y + hh); g.lineTo(x + hw, y); g.lineTo(x, y - hh); g.lineTo(x - hw, y); g.closePath(); };
  const cartouche = (inset) => { g.beginPath(); g.ellipse(0, 0, 14.5 - inset, 22 - inset, 0, 0, 2 * Math.PI); };

  g.fillStyle = '#fff'; g.fillRect(-W / 2, -H / 2, W, H);

  // 20 LPI-style checkering over everything, straight across the rounded edges and into the bleed
  g.save(); g.strokeStyle = '#000'; g.lineWidth = 0.32;
  for (const a of [Math.PI / 2 - 0.62, Math.PI / 2 + 0.62]) {
    const dx = Math.cos(a), dy = Math.sin(a);
    for (let k = -120; k <= 120; k += 1.27) {
      g.beginPath(); g.moveTo(k * dy - 80 * dx, -k * dx - 80 * dy); g.lineTo(k * dy + 80 * dx, -k * dx + 80 * dy); g.stroke();
    }
  }
  g.restore();

  // smooth double diamonds around the screw bushings
  for (const y of [38.5, -38.5]) {
    diamond(1, y, 10, 12.6); g.fillStyle = '#fff'; g.fill(); g.lineWidth = 0.6; g.strokeStyle = '#000'; g.stroke();
    diamond(1, y, 8.6, 10.9); g.lineWidth = 0.22; g.stroke();
    diamond(1, y, 5.8, 7.6); g.fillStyle = '#7a7a7a'; g.fill(); // mid-grey: a shallow stippled field
    g.beginPath(); g.arc(1, y, 4.3, 0, 2 * Math.PI); g.fillStyle = '#fff'; g.fill();
  }

  // centre cartouche: title, grey ramp, grey dots
  cartouche(0); g.fillStyle = '#fff'; g.fill(); g.lineWidth = 0.7; g.strokeStyle = '#000'; g.stroke();
  cartouche(1.3); g.lineWidth = 0.25; g.stroke();
  text('1911', 0, 9.5, 10.5);
  text('EMUCRAFT', 0, 1.8, 3.6, 'bold', '#000', 0.35);
  text('LASER SIMULATION', 0, -2.4, 1.6, 'normal', '#000', 0.15);
  const ramp = g.createLinearGradient(-10, 0, 10, 0); ramp.addColorStop(0, '#fff'); ramp.addColorStop(1, '#000');
  g.fillStyle = ramp; g.fillRect(-10, -7.6, 20, 2.6); g.lineWidth = 0.15; g.strokeStyle = '#000'; g.strokeRect(-10, -7.6, 20, 2.6);
  [0.2, 0.4, 0.6, 0.8, 1].forEach((v, k) => { const q = Math.round(255 * (1 - v));
    g.beginPath(); g.arc(-8 + k * 4, -12, 1.4, 0, 2 * Math.PI); g.fillStyle = `rgb(${q},${q},${q})`; g.fill(); });
  text('GREY 20 · 40 · 60 · 80 · 100 %', 0, -15.2, 1.05, 'normal');

  // solid band right over the edge round-over (outline sits at x = -21.5 / ~21; panel ends y = +-54)
  g.fillStyle = '#000';
  g.fillRect(-W / 2, -H / 2, 3.2, H); // front edge band from the bleed into the curve
  g.fillRect(W / 2 - 3.6, -H / 2, 3.6, H); // rear edge band
  // fine text that runs onto the bottom round-over
  g.fillStyle = '#fff'; g.fillRect(-18.5, -54.6, 37, 3.5);
  text('CAL .45 ACP · GOVERNMENT MODEL', 0, -52.8, 1.45, 'bold', '#000', 0.08);
  return c.toDataURL('image/png');
});
fs.writeFileSync(out, Buffer.from(url.split(',')[1], 'base64'));
console.log(out);
await b.close();
