import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import * as THREE from 'three';
import { PNG } from 'pngjs';
import validator from 'gltf-validator';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { DEFAULT_LASER } from '../src/sim.js';
import { triangle_soup } from '../src/part.js';
import { make_mcp_server } from '../mcp.mjs';

// 30 x 20 x 4 mm plate as binary STL, and a 60 x 40 px PNG with a black square in the middle
const stl = () => {
  const shape = new THREE.Shape().moveTo(-15, -10).lineTo(15, -10).lineTo(15, 10).lineTo(-15, 10).lineTo(-15, -10);
  const p = triangle_soup(new THREE.Mesh(new THREE.ExtrudeGeometry(shape, { depth: 4, bevelEnabled: false })));
  const buf = Buffer.alloc(84 + (p.length / 9) * 50);
  buf.writeUInt32LE(p.length / 9, 80);
  for (let t = 0; t < p.length / 9; t++) for (let k = 0; k < 9; k++) buf.writeFloatLE(p[t * 9 + k], 84 + t * 50 + 12 + k * 4);
  return buf;
};
const png = () => {
  const data = Buffer.alloc(60 * 40 * 4, 255);
  for (let j = 10; j < 30; j++) for (let i = 15; i < 45; i++) data.fill(0, (j * 60 + i) * 4, (j * 60 + i) * 4 + 3);
  return PNG.sync.write({ width: 60, height: 40, data }, { colorType: 6 });
};

const connect = async (url, headers = {}) => {
  const client = new Client({ name: 'test', version: '1' });
  await client.connect(new StreamableHTTPClientTransport(new URL(url), { requestInit: { headers } }));
  return client;
};

describe('laser MCP server', () => {
  let server, url, client;
  beforeAll(async () => {
    server = make_mcp_server({ jobs: 1, timeout_s: 60 });
    await new Promise((r) => server.listen(0, '127.0.0.1', r));
    url = `http://127.0.0.1:${server.address().port}`;
    client = await connect(`${url}/mcp`);
  });
  afterAll(async () => { await client.close(); server.close(); });
  const job = () => ({
    model_format: 'stl', model_data: stl().toString('base64'), image_data: png().toString('base64'),
    placement: { angle_deg: 10, width_mm: 24 }, output: { mesh_mm: 1, max_texture: 512 },
  });

  it('answers the health check and lists its tools', async () => {
    expect(await (await fetch(`${url}/health`)).json()).toEqual({ status: 'ok', running: 0 });
    expect((await client.listTools()).tools.map((t) => t.name)).toEqual(['laser_info', 'render_glb']);
  });

  it('lists presets and defaults', async () => {
    const { structuredContent } = await client.callTool({ name: 'laser_info', arguments: {} });
    expect(structuredContent.presets.super_rosewood).toMatch(/Rosewood/);
    expect(structuredContent.laser).toEqual(DEFAULT_LASER);
  });

  it('renders a job to a valid GLB with its summary', async () => {
    const r = await client.callTool({ name: 'render_glb', arguments: job() });
    expect(r.isError).toBeFalsy();
    const [summary, file] = r.content;
    expect(JSON.parse(summary.text).max_depth_mm).toBeGreaterThan(0);
    expect(file.resource.mimeType).toBe('model/gltf-binary');
    const report = await validator.validateBytes(new Uint8Array(Buffer.from(file.resource.blob, 'base64')));
    expect(report.issues.numErrors).toBe(0);
  }, 60000);

  it('reports bad jobs as tool errors with the reason', async () => {
    const r = await client.callTool({ name: 'render_glb', arguments: { ...job(), material: { preset: 'cheese' } } });
    expect(r.isError).toBe(true);
    expect(r.content[0].text).toMatch(/unknown material.preset 'cheese'/);
  }, 60000);

  it('checks the bearer token when one is set', async () => {
    const locked = make_mcp_server({ token: 'sesame' });
    await new Promise((r) => locked.listen(0, '127.0.0.1', r));
    const at = `http://127.0.0.1:${locked.address().port}/mcp`;
    expect((await fetch(at, { method: 'POST', body: '{}' })).status).toBe(401);
    const ok = await connect(at, { Authorization: 'Bearer sesame' });
    expect((await ok.listTools()).tools).toHaveLength(2);
    await ok.close();
    locked.close();
  });
});
