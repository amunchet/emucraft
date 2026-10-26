// Web Worker: runs one engraving job off the main thread and posts the grids back as transferred buffers.
import { simulate } from './sim.js';

self.onmessage = (e) => {
  try {
    const res = simulate(e.data, (fraction) => self.postMessage({ type: 'progress', fraction }));
    self.postMessage({ type: 'done', ...res }, [res.surface.buffer, res.height.buffer, res.depth.buffer]);
  } catch (err) {
    console.error(err);
    self.postMessage({ type: 'error', message: err.message || String(err) });
  }
};
