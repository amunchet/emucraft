import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import * as THREE from 'three';
import { PNG } from 'pngjs';
import validator from 'gltf-validator';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { DEFAULT_LASER } from '../src/sim.js';
import { triangle_soup } from '../src/part.js';
import { make_mcp_server } from '../mcp.mjs';
import { make_server } from '../server.mjs';

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
  let server, url, client, templates;
  beforeAll(async () => {
    templates = fs.mkdtempSync(path.join(os.tmpdir(), 'laser-templates-'));
    server = make_mcp_server({ jobs: 1, timeout_s: 60, templates, secret: 'shared' });
    await new Promise((r) => server.listen(0, '127.0.0.1', r));
    url = `http://127.0.0.1:${server.address().port}`;
    client = await connect(`${url}/mcp`);
  });
  afterAll(async () => { await client.close(); server.close(); fs.rmSync(templates, { recursive: true }); });
  const job = () => ({
    model_format: 'stl', model_data: stl().toString('base64'), image_data: png().toString('base64'),
    placement: { angle_deg: 10, width_mm: 24 }, output: { mesh_mm: 1, max_texture: 512 },
  });

  it('answers the health check and lists its tools', async () => {
    expect(await (await fetch(`${url}/health`)).json()).toEqual({ status: 'ok', running: 0 });
    expect((await client.listTools()).tools.map((t) => t.name)).toEqual(['laser_info', 'render_glb', 'list_templates', 'upload_template', 'delete_template']);
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
    expect((await ok.listTools()).tools).toHaveLength(5);
    expect((await fetch(`http://127.0.0.1:${locked.address().port}/templates/x.stl`, { method: 'PUT', body: stl() })).status).toBe(401);
    await ok.close();
    locked.close();
  });

  const call = (name, args) => client.callTool({ name, arguments: args });

  it('stores a template, renders it by id, and only ever reports it by name and size', async () => {
    const up = await call('upload_template', { id: 'colt_python', format: 'stl', data: stl().toString('base64') });
    expect(up.isError).toBeFalsy();
    expect(up.structuredContent.result).toMatchObject({ id: 'colt_python', format: 'stl', triangles: 12, replaced: false });
    const again = await call('upload_template', { id: 'colt_python', format: 'stl', data: stl().toString('base64') });
    expect(again.isError).toBe(true);
    expect(again.content[0].text).toMatch(/already exists/);

    const [listed] = (await call('list_templates', {})).structuredContent.result;
    expect(Object.keys(listed).sort()).toEqual(['bytes', 'format', 'id', 'uploaded']);
    expect(listed).toMatchObject({ id: 'colt_python', format: 'stl', bytes: stl().length });

    const { model_format, model_data, ...rest } = job();
    const r = await call('render_glb', { ...rest, template: 'colt_python' });
    expect(r.isError).toBeFalsy();
    // the same stand-in as rendering the part itself
    const direct = await call('render_glb', job());
    expect(r.content[1].resource.blob).toBe(direct.content[1].resource.blob);
  }, 120000);

  it('never gives a template back', async () => {
    await call('upload_template', { id: 'm1911', format: 'stl', data: stl().toString('base64'), replace: true });
    const { model_format, model_data, ...rest } = job();
    for (const [args, msg] of [
      [{ ...rest, template: 'm1911', output: { true_scale: true } }, /true_scale is not available for templates/],
      [{ ...rest, template: '../m1911' }, /template id must be/],
      [{ ...rest, template: 'nope' }, /unknown template 'nope'/],
      [{ ...job(), template: 'm1911' }, /either a template or format and data/],
    ]) {
      const r = await call('render_glb', args);
      expect(r.isError, JSON.stringify(args.template)).toBe(true);
      expect(r.content[0].text).toMatch(msg);
    }
    for (const method of ['GET', 'POST']) expect((await fetch(`${url}/templates/m1911.stl`, { method })).status).toBe(405);
    expect((await fetch(`${url}/templates/m1911`)).status).toBe(404);
    expect(fs.statSync(path.join(templates, 'm1911.stl')).mode & 0o077).toBe(0);
  }, 60000);

  it('takes big templates by PUT, and deletes them', async () => {
    const put = (name, body, q = '') => fetch(`${url}/templates/${name}${q}`, { method: 'PUT', body });
    expect(await (await put('grip.stl', stl())).json()).toMatchObject({ id: 'grip', format: 'stl', triangles: 12 });
    expect((await put('grip.stl', stl())).status).toBe(409);
    expect((await put('Bad..id.stl', stl())).status).toBe(400);
    expect((await put('junk.stl', Buffer.from('not a model'))).status).toBe(400);
    expect(fs.existsSync(path.join(templates, 'junk.stl'))).toBe(false);
    expect((await put('grip.stl', stl(), '?replace=1')).status).toBe(200);

    expect((await call('delete_template', { id: 'grip' })).structuredContent.result).toEqual({ id: 'grip', deleted: true });
    expect((await call('list_templates', {})).structuredContent.result.map((t) => t.id)).not.toContain('grip');
    expect((await call('delete_template', { id: 'grip' })).content[0].text).toMatch(/unknown template 'grip'/);
  });

  it('lets the API render the same templates', async () => {
    await call('upload_template', { id: 'shared_part', format: 'stl', data: stl().toString('base64'), replace: true });
    const api = make_server({ templates, secret: 'shared' });
    await new Promise((r) => api.listen(0, '127.0.0.1', r));
    const { model_format, model_data, image_data, ...rest } = job();
    const r = await fetch(`http://127.0.0.1:${api.address().port}/api/laser/render`, {
      method: 'POST', body: JSON.stringify({ ...rest, model: { template: 'shared_part' }, image: { data: image_data } }),
    });
    expect(r.status).toBe(200);
    const glb = Buffer.from(await r.arrayBuffer());
    api.close();
    const direct = await call('render_glb', job());
    expect(glb.toString('base64')).toBe(direct.content[1].resource.blob);
  }, 120000);
});
