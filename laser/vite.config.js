// Two pages: the simulator app and the reference customer viewer. Relative asset URLs, so the build works under any
// path prefix (/laser-simulator/).
export default { base: './', build: { rolldownOptions: { input: { main: 'index.html', viewer: 'viewer.html' } } } };
