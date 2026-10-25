// Web Worker: runs one engraving job off the main thread and posts the grids back as transferred buffers.
import { plan_grid, resample_image, grey_to_intensity, rasterize_surface, engrave } from './sim.js';

self.onmessage = (e) => {
  try {
    const { positions, grey, img_w, img_h, placement, laser, supersample } = e.data;
    const { grid_w, grid_h } = plan_grid(img_w, img_h, supersample);
    const intensity = grey_to_intensity(resample_image(grey, img_w, img_h, grid_w, grid_h), laser);
    const surface = rasterize_surface(positions, placement, grid_w, grid_h);
    if (surface.every(Number.isNaN)) throw new Error('the image footprint does not overlap the model');
    const pixel_mm = (placement.width_mm / grid_w + placement.height_mm / grid_h) / 2;
    const res = engrave(surface, intensity, grid_w, grid_h, pixel_mm, laser, (fraction) => self.postMessage({ type: 'progress', fraction }));
    self.postMessage(
      { type: 'done', grid_w, grid_h, surface, height: res.height, depth: res.depth, max_depth: res.max_depth, stats: res.stats },
      [surface.buffer, res.height.buffer, res.depth.buffer],
    );
  } catch (err) {
    console.error(err);
    self.postMessage({ type: 'error', message: err.message || String(err) });
  }
};
