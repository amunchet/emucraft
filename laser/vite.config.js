// Two pages: the simulator app and the reference customer viewer. Relative asset URLs, so the build works under any
// path prefix (/laser-simulator/). In dev, the template routes go to a local `node server.mjs`, as nginx does in the image.
export default {
  base: './', build: { rolldownOptions: { input: { main: 'index.html', viewer: 'viewer.html' } } },
  server: { proxy: { '/api/laser/templates': 'http://127.0.0.1:8001' } },
};
