// Laser MCP server: the laser API's engraving jobs (server.mjs) as MCP tools, over streamable HTTP at /mcp.
//
//   laser_info       presets, laser defaults and option defaults
//   render_glb       model (base64) or template + image (base64) -> the customer GLB as an embedded resource, and its summary
//   list_templates   the stored templates: id, format, bytes, upload time
//   upload_template  store a proprietary master part (base64) under an id; PUT /templates/<id>.<stl|obj> does the same
//                    with the raw file, for parts too big to pass through a tool call
//   delete_template  remove one
//
// Templates are write-only here: no tool reads one back. Only the simulator app loads them, through the API (server.mjs).
// The job is the API's, so a render here is the same stand-in part as from POST /api/laser/render: give this server
// the API's LASER_SECRET, or the two would scale a part by different factors and narrow down its real size.
// Stateless: each request gets its own MCP server and transport. GET /health answers for the container.
//
// Run: node mcp.mjs   (env: HOST=127.0.0.1, PORT=8002, LASER_JOBS=2, LASER_TIMEOUT_S=300, LASER_MAX_MB=256,
//   LASER_TOKEN unset = no auth (leave it unset behind Caddy, which removes the bearer token), LASER_SECRET,
//   LASER_TEMPLATES=templates)
import http from 'node:http';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { z } from 'zod';
import { info, run_job, list_templates, save_template, delete_template } from './server.mjs';

const INSTRUCTIONS = `Laser engraving simulator: engrave an image onto a 3D part with a modelled laser and get the result as a GLB
for a three.js viewer. laser_info lists the material presets and the laser and option defaults; render_glb runs a job.
Placement is in the simulator frame: the model centred in XY, its lowest point at Z = 0, engraved from above (+Z).
Unless output.true_scale is set, the GLB is a faithful render of a stand-in part scaled by a fixed factor in [0.9, 1.1].
Templates are stored proprietary parts (a 1911 frame, a Colt Python, ...): upload_template stores one under an id,
render_glb engraves it with \`template\` in place of model_format/model_data, delete_template removes it. They cannot be
downloaded over MCP, and their renders are always the stand-in.`;

const n = (what) => z.number().optional().describe(what);
const job_shape = {
  model_format: z.enum(['stl', 'obj']).optional().describe('format of model_data'),
  model_data: z.string().optional().describe('the part, base64'),
  template: z.string().optional().describe('a stored template id from list_templates, instead of model_format and model_data'),
  image_data: z.string().describe('the engraving, a PNG or JPEG, base64; dark marks, transparent pixels are left alone'),
  placement: z.object({
    cx: n('image centre X, mm (default 0)'), cy: n('image centre Y, mm (default 0)'), angle_deg: n('rotation about Z (default 0)'),
    width_mm: n('engraved width (default: 1 px = 1 laser spot)'), height_mm: n('engraved height (default: keeps the image aspect)'),
  }).passthrough().optional(),
  laser: z.record(z.string(), z.union([z.number(), z.boolean()])).optional()
    .describe('overrides of the laser defaults from laser_info, e.g. {"power_w": 20, "passes": 4, "invert": false}'),
  supersample: n('engraving grid cells per image pixel along each side, 1-4 (default 2)'),
  material: z.object({
    preset: z.string().optional().describe('a preset key from laser_info (default anodized_black)'),
    color: z.string().optional().describe('#rrggbb base colour'), mark: z.string().optional().describe('#rrggbb colour of the marks'),
    rough: n('0-1'), metal: n('0-1'), mark_rough: n('0-1'), mark_metal: n('0-1'), tex_scale_mm: n('texture tile size, mm'),
    texture: z.string().optional().describe('own texture tile, PNG or JPEG, base64'),
  }).passthrough().optional(),
  output: z.object({
    mesh_mm: n('relief mesh pitch, 0.1-2 mm (default 0.5)'), max_texture: n('largest texture side, 256-8192 px (default 2048)'),
    true_scale: z.boolean().optional().describe('render the real part instead of the stand-in (default false)'),
  }).passthrough().optional(),
};

// One MCP server per request; `run` runs a job body, `templates` is the template directory.
function mcp_server(run, templates) {
  const server = new McpServer({ name: 'laser', version: '0.1.0' }, { instructions: INSTRUCTIONS });
  const text = (value) => ({ type: 'text', text: JSON.stringify(value, null, 2) });
  const failed = (err) => {
    const internal = !err.status || err.status >= 500;
    if (internal && err.status !== 504) console.error(err);
    return { isError: true, content: [{ type: 'text', text: internal && err.status !== 504 ? 'internal error (see the server log)' : err.message }] };
  };
  const answer = (fn) => async (args) => {
    try { const value = fn(args); return { content: [text(value)], structuredContent: { result: value } }; } catch (err) { return failed(err); }
  };
  server.registerTool('laser_info', { description: 'Material presets, the laser defaults and the option defaults for render_glb.' },
    async () => ({ content: [text(info())], structuredContent: info() }));
  server.registerTool('render_glb', {
    description: 'Engrave an image onto a part and return the customer GLB (model/gltf-binary, as an embedded resource) with its '
      + 'summary: texture size, triangle counts, deepest engraving, file size. Rendering takes seconds to minutes.',
    inputSchema: job_shape,
  }, async ({ model_format, model_data, template, image_data, ...rest }, extra) => {
    const job = { model: { format: model_format, data: model_data, template }, image: { data: image_data }, ...rest };
    try {
      const { glb, summary } = await run(new TextEncoder().encode(JSON.stringify(job)), extra.signal);
      return {
        content: [text(summary), { type: 'resource', resource: { uri: 'laser://part.glb', mimeType: 'model/gltf-binary', blob: Buffer.from(glb.buffer, glb.byteOffset, glb.byteLength).toString('base64') } }],
      };
    } catch (err) {
      return failed(err);
    }
  });
  server.registerTool('list_templates', { description: 'The stored templates: id, format, file size and upload time. Their geometry is never shown.' },
    answer(() => list_templates(templates)));
  server.registerTool('upload_template', {
    description: 'Store a proprietary part as a template under an id, for render_glb. It can be replaced or deleted but not downloaded. '
      + 'For a big file, PUT the raw file to /templates/<id>.<stl|obj> on this server instead (same bearer token; ?replace=1 to overwrite).',
    inputSchema: {
      id: z.string().describe('template id: 1-64 of a-z, 0-9, _ and -, e.g. "colt_python"'),
      format: z.enum(['stl', 'obj']), data: z.string().describe('the part, base64'),
      replace: z.boolean().optional().describe('overwrite an existing template with this id (default false)'),
    },
  }, answer(({ id, format, data, replace }) => save_template(templates, id, format, Buffer.from(data, 'base64'), replace)));
  server.registerTool('delete_template', { description: 'Delete a stored template.', inputSchema: { id: z.string() } },
    answer(({ id }) => delete_template(templates, id)));
  return server;
}

// HTTP server: POST /mcp (stateless streamable HTTP), PUT /templates/<id>.<stl|obj>, GET /health. Jobs beyond `jobs`
// concurrent ones are refused.
export function make_mcp_server({ jobs = 2, timeout_s = 300, max_mb = 256, token = '', secret = randomBytes(32), templates = 'templates' } = {}) {
  const max_body = max_mb * 1024 * 1024, auth = Buffer.from(`Bearer ${token}`);
  let running = 0;
  const run = async (body, signal) => {
    if (running >= jobs) throw Object.assign(new Error('busy: too many jobs running, retry shortly'), { status: 503 });
    running++;
    try {
      return await run_job(body, { secret, templates, timeout_s, signal });
    } finally {
      running--;
    }
  };
  const json = (out, status, value) => { out.writeHead(status, { 'Content-Type': 'application/json' }); out.end(JSON.stringify(value)); };
  const same = (header, want) => { const got = Buffer.from(header || ''); return got.length === want.length && timingSafeEqual(got, want); };
  const server = http.createServer(async (req, out) => {
    const url = new URL(req.url, 'http://x'), path = url.pathname, put = /^\/templates\/([^/]+)\.(stl|obj)$/.exec(path);
    try {
      if (req.method === 'GET' && path === '/health') return json(out, 200, { status: 'ok', running });
      if (path !== '/mcp' && !put) return json(out, 404, { error: 'not found' });
      if (token && !same(req.headers.authorization, auth)) return json(out, 401, { error: 'missing or wrong bearer token' });
      // stateless: no sessions to resume or end; templates only go in
      if (req.method !== (put ? 'PUT' : 'POST')) return json(out, 405, { jsonrpc: '2.0', error: { code: -32000, message: 'Method not allowed' }, id: null });
      // read the body here: the transport's own reader stops at 4 MB, too small for a model in base64
      const parts = [];
      let size = 0;
      for await (const chunk of req) {
        size += chunk.length;
        if (size > max_body) { out.setHeader('Connection', 'close'); return json(out, 413, { error: `body over ${max_mb} MB` }); }
        parts.push(chunk);
      }
      if (put) {
        try {
          return json(out, 200, save_template(templates, put[1], put[2], Buffer.concat(parts), ['1', 'true'].includes(url.searchParams.get('replace'))));
        } catch (err) {
          if (!err.status) throw err;
          return json(out, err.status, { error: err.message });
        }
      }
      let body;
      try { body = JSON.parse(Buffer.concat(parts).toString('utf8')); } catch { return json(out, 400, { jsonrpc: '2.0', error: { code: -32700, message: 'Parse error' }, id: null }); }
      const server = mcp_server(run, templates), transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true });
      out.on('close', () => { transport.close(); server.close(); });
      await server.connect(transport);
      await transport.handleRequest(req, out, body);
    } catch (err) {
      console.error(err);
      if (!out.headersSent && !out.destroyed) json(out, 500, { error: 'internal error (see the server log)' });
    }
  });
  server.requestTimeout = 30 * 60 * 1000; // node's 5 min default cuts off a big template upload on a slow link
  return server;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const env = process.env, host = env.HOST || '127.0.0.1', port = Number(env.PORT || 8002);
  make_mcp_server({ jobs: Number(env.LASER_JOBS || 2), timeout_s: Number(env.LASER_TIMEOUT_S || 300), max_mb: Number(env.LASER_MAX_MB || 256), token: env.LASER_TOKEN || '', templates: env.LASER_TEMPLATES || 'templates', ...(env.LASER_SECRET && { secret: env.LASER_SECRET }) }).listen(port, host, () => console.log(`laser MCP on http://${host}:${port}/mcp`));
}
