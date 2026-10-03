/**
 * Small three.js helpers shared by the 3D views: text that always faces the
 * camera, a colour from the live theme, and tearing a scene down without
 * leaking GPU memory when a page is left.
 */
import * as THREE from 'three';

export function color(css: string): THREE.Color {
  return new THREE.Color(css);
}

/** A text label as a camera-facing sprite. `size` is its height in world units. */
export function label(text: string, css: string, size = 2.2, weight = 500): THREE.Sprite {
  const scale = 4;
  const font = `${weight} ${16 * scale}px Inter, "Segoe UI", system-ui, sans-serif`;
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d')!;
  ctx.font = font;
  const w = Math.ceil(ctx.measureText(text).width) + 12 * scale;
  const h = 26 * scale;
  canvas.width = w;
  canvas.height = h;
  ctx.font = font;
  ctx.fillStyle = css;
  ctx.textBaseline = 'middle';
  ctx.fillText(text, 6 * scale, h / 2);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.minFilter = THREE.LinearFilter;
  const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false });
  const sprite = new THREE.Sprite(mat);
  sprite.scale.set((size * w) / h, size, 1);
  sprite.center.set(0, 0.5);
  return sprite;
}

export function dispose(root: THREE.Object3D): void {
  root.traverse(obj => {
    const o = obj as any;
    o.geometry?.dispose?.();
    const mats = Array.isArray(o.material) ? o.material : o.material ? [o.material] : [];
    for (const m of mats) {
      m.map?.dispose?.();
      m.dispose?.();
    }
  });
}

/** WebGL is available here (it is not in some remote desktops and old drivers). */
export function webglAvailable(): boolean {
  try {
    const c = document.createElement('canvas');
    return !!(c.getContext('webgl2') || c.getContext('webgl'));
  } catch {
    return false;
  }
}
