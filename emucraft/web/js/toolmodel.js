// Cutter, shank and holder built from the same tool data the kernel uses.
import * as THREE from 'three';
import { toolColor } from './util.js';

function cutterProfile(spec) {
  const R = spec.diameter / 2;
  const flute = spec.flute_length || Math.max(spec.diameter * 2.5, R * 4);
  const pts = [new THREE.Vector2(0, 0)];
  if (spec.shape === 'ball') {
    for (let k = 1; k <= 12; k++) {
      const a = (k / 12) * Math.PI / 2;
      pts.push(new THREE.Vector2(R * Math.sin(a), R - R * Math.cos(a)));
    }
  } else if (spec.shape === 'bull' && spec.corner_radius > 0) {
    const r = spec.corner_radius, rf = R - r;
    pts.push(new THREE.Vector2(rf, 0));
    for (let k = 1; k <= 8; k++) {
      const a = (k / 8) * Math.PI / 2;
      pts.push(new THREE.Vector2(rf + r * Math.sin(a), r - r * Math.cos(a)));
    }
  } else if (spec.shape === 'cone') {
    const tip = spec.corner_radius || 0;
    const slope = 1 / Math.tan((spec.tip_angle || 118) * Math.PI / 360);
    if (tip > 0) pts.push(new THREE.Vector2(tip, 0));
    pts.push(new THREE.Vector2(R, (R - tip) * slope));
  } else {
    pts.push(new THREE.Vector2(R, 0));
  }
  pts.push(new THREE.Vector2(R, Math.max(flute, pts[pts.length - 1].y)));
  pts.push(new THREE.Vector2(0, Math.max(flute, pts[pts.length - 2].y)));
  return { pts, flute };
}

function lathe(points, material) {
  const geom = new THREE.LatheGeometry(points, 40);
  geom.rotateX(Math.PI / 2); // lathe axis Y -> Z
  return new THREE.Mesh(geom, material);
}

export function buildTool(spec, slot) {
  const group = new THREE.Group();
  group.name = `T${spec.number}`;
  if (!spec.diameter) return group;
  const R = spec.diameter / 2;
  const color = new THREE.Color(toolColor(slot));
  const { pts, flute } = cutterProfile(spec);
  group.add(lathe(pts, new THREE.MeshStandardMaterial({ color, metalness: 0.6, roughness: 0.35 })));
  const stickout = spec.stickout || flute + spec.diameter * 2;
  if (stickout > flute) {
    const rs = (spec.shank_diameter || spec.diameter) / 2;
    const shank = new THREE.CylinderGeometry(rs, rs, stickout - flute, 32, 1);
    shank.rotateX(Math.PI / 2);
    shank.translate(0, 0, (stickout + flute) / 2);
    group.add(new THREE.Mesh(shank, new THREE.MeshStandardMaterial({ color: 0x9aa3ad, metalness: 0.7, roughness: 0.3 })));
  }
  const holderR = spec.holder_diameter ? spec.holder_diameter / 2 : Math.max(R * 2.2, R + 0.25 * spec.diameter);
  const holderLen = spec.holder_length || holderR * 2.5;
  const holderMat = new THREE.MeshStandardMaterial({
    color: spec.holder_diameter ? 0x3b82c4 : 0x6b7280, metalness: 0.3, roughness: 0.6,
    transparent: true, opacity: spec.holder_diameter && spec.stickout ? 0.35 : 0.15, depthWrite: false,
  });
  const holder = new THREE.CylinderGeometry(holderR, holderR, holderLen, 48, 1, false);
  holder.rotateX(Math.PI / 2);
  holder.translate(0, 0, stickout + holderLen / 2);
  const holderMesh = new THREE.Mesh(holder, holderMat);
  holderMesh.renderOrder = 3;
  group.add(holderMesh);
  const edges = new THREE.LineSegments(new THREE.EdgesGeometry(holder, 30),
    new THREE.LineBasicMaterial({ color: 0x3b82c4, transparent: true, opacity: 0.6 }));
  group.add(edges);
  return group;
}

export function disposeTool(group) {
  group.traverse(o => {
    if (o.geometry) o.geometry.dispose();
    if (o.material) o.material.dispose();
  });
}
