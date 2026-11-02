// Scene layers: the textured meshes and particle systems of a Unity prefab, in three.js axes. The battleboards and the
// home backgrounds both use them. Unity is left-handed, so Z is mirrored and winding flips.

export function quatMatrix(q, s, p) {
  const [x, y, z, w] = [q.x, q.y, q.z, q.w];
  const xx = x * x, yy = y * y, zz = z * z, xy = x * y, xz = x * z, yz = y * z, wx = w * x, wy = w * y, wz = w * z;
  return [
    (1 - 2 * (yy + zz)) * s.x, 2 * (xy + wz) * s.x, 2 * (xz - wy) * s.x, 0,
    2 * (xy - wz) * s.y, (1 - 2 * (xx + zz)) * s.y, 2 * (yz + wx) * s.y, 0,
    2 * (xz + wy) * s.z, 2 * (yz - wx) * s.z, (1 - 2 * (xx + yy)) * s.z, 0,
    p.x, p.y, p.z, 1,
  ];
}

export function mul(a, b) {
  const out = new Array(16);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
    let v = 0;
    for (let k = 0; k < 4; k++) v += a[k * 4 + r] * b[c * 4 + k];
    out[c * 4 + r] = v;
  }
  return out;
}

export const minMax = (m) => (m.minMaxState === 2 ? [m.minScalar, m.scalar] : [m.scalar, m.scalar]);

// Colour keys of a Unity gradient as sorted { t, rgba } stops, t in 0..1.
function gradientStops(gradient) {
  const stops = [];
  for (let i = 0; i < gradient.m_NumColorKeys; i++) {
    const key = gradient[`key${i}`];
    stops.push({ t: gradient[`ctime${i}`] / 65535, rgba: [key.r, key.g, key.b, key.a] });
  }
  return stops.sort((a, b) => a.t - b.t);
}

export function emitterFrom(system, position, texture, name) {
  const init = system.InitialModule, emission = system.EmissionModule, shape = system.ShapeModule, color = system.ColorModule;
  const start = init.startColor.maxColor;
  return {
    name, position, texture,
    looping: !!system.looping,
    duration: system.lengthInSec,
    delay: minMax(system.startDelay)[1],
    rate: emission.enabled ? minMax(emission.rateOverTime) : [0, 0],
    bursts: emission.enabled ? emission.m_Bursts.map((b) => ({ time: b.time, count: [b.minCount, b.maxCount] })) : [],
    lifetime: minMax(init.startLifetime),
    speed: minMax(init.startSpeed),
    size: minMax(init.startSize),
    startColor: [start.r, start.g, start.b, start.a],
    gradient: color.enabled && color.gradient.minMaxState === 1 ? gradientStops(color.gradient.maxGradient) : null,
    shape: {
      type: shape.enabled ? shape.type : -1,
      radius: shape.radius.value, angle: shape.angle, length: shape.length,
      box: [shape.boxX, shape.boxY, shape.boxZ],
    },
    maxParticles: init.maxNumParticles,
  };
}

// -- Backgrounds -------------------------------------------------------------

import { decodeTexture, decodeMesh } from "./media.js";

const CLASS = { GameObject: 1, Transform: 4, MeshFilter: 33, MeshRenderer: 23, ParticleSystem: 198, ParticleSystemRenderer: 199 };

// The layers of a Background_* prefab (resources.assets): every textured MeshRenderer under its root, placed where the
// prefab places it, and its particle systems. Animators and scripts are not run, so animated layers stay where authored.
// Returns { name, parts, emitters, bounds } in three.js axes, or null when the prefab is not in the file.
// ``readStream(path, offset, size)`` reads a range of a stream file (the textures' pixels, in resources.assets.resS).
export async function backgroundLayers(serialized, prefabName, readStream) {
  const byId = (classId) => serialized.objects.filter((o) => o.classId === classId);
  const transformById = new Map(), transformByGo = new Map();
  for (const o of byId(CLASS.Transform)) {
    const t = serialized.read(o);
    transformById.set(o.pathId, t);
    transformByGo.set(t.m_GameObject.m_PathID, t);
  }
  const root = byId(CLASS.GameObject).find((o) => serialized.read(o).m_Name === prefabName);
  if (!root || !transformByGo.has(root.pathId)) return null;

  // Every GameObject under the root, by walking the transform children.
  const inside = [];
  const visit = (transform, depth) => {
    inside.push(transform.m_GameObject.m_PathID);
    if (depth > 64) return;
    for (const child of transform.m_Children) {
      const next = transformById.get(child.m_PathID);
      if (next) visit(next, depth + 1);
    }
  };
  visit(transformByGo.get(root.pathId), 0);

  const worldOf = (transform, depth = 0) => {
    const local = quatMatrix(transform.m_LocalRotation, transform.m_LocalScale, transform.m_LocalPosition);
    const father = transformById.get(transform.m_Father.m_PathID);
    if (!father || depth > 64) return local;
    return mul(worldOf(father, depth + 1), local);
  };

  const textures = new Map();
  // A material's albedo texture, decoded. Large textures keep their pixels in a stream file, read by range.
  const textureOf = async (materialId) => {
    const material = serialized.get(materialId);
    if (!material || material.classId !== 21) return null;
    const env = serialized.read(material).m_SavedProperties.m_TexEnvs.find((e) => e.first === "_MainTex");
    const texId = env ? env.second.m_Texture.m_PathID : 0;
    if (!texId) return null;
    if (!textures.has(texId)) {
      const object = serialized.get(texId);
      let pixels = null;
      if (object && object.classId === 28) {
        const texture = serialized.read(object);
        const inline = texture["image data"];
        const stream = texture.m_StreamData;
        const data = inline && inline.length ? inline
          : stream && stream.size ? await readStream(stream.path, stream.offset, stream.size) : null;
        pixels = data ? decodeTexture(texture, data) : null;
      }
      textures.set(texId, pixels);
    }
    return textures.get(texId);
  };
  const tintOf = (materialId) => {
    const material = serialized.read(serialized.get(materialId));
    const entry = material.m_SavedProperties.m_Colors.find((e) => e.first === "_Color");
    return entry ? [entry.second.r, entry.second.g, entry.second.b] : [1, 1, 1];
  };

  // Unity's left-handed world to three.js: the Z axis is mirrored.
  const toThree = (positions, w) => {
    const out = new Float32Array(positions.length);
    for (let i = 0; i < positions.length; i += 3) {
      const x = positions[i], y = positions[i + 1], z = positions[i + 2];
      out[i] = w[0] * x + w[4] * y + w[8] * z + w[12];
      out[i + 1] = w[1] * x + w[5] * y + w[9] * z + w[13];
      out[i + 2] = -(w[2] * x + w[6] * y + w[10] * z + w[14]);
    }
    return out;
  };
  // Mirroring flips each triangle's winding.
  const flip = (indices) => {
    const out = new Uint32Array(indices.length);
    for (let i = 0; i < indices.length; i += 3) {
      out[i] = indices[i]; out[i + 1] = indices[i + 2]; out[i + 2] = indices[i + 1];
    }
    return out;
  };

  const parts = [], emitters = [];
  for (const goId of inside) {
    const go = serialized.read(serialized.get(goId));
    if (!go.m_IsActive) continue;
    const transform = transformByGo.get(goId);
    if (!transform) continue;
    const world = worldOf(transform);
    const components = go.m_Component.map((c) => serialized.get(c.component.m_PathID)).filter(Boolean);
    const classOf = (o) => o.classId;
    const filter = components.find((o) => classOf(o) === CLASS.MeshFilter);
    const renderer = components.find((o) => classOf(o) === CLASS.MeshRenderer);
    if (filter && renderer) {
      const meshRef = serialized.read(filter).m_Mesh;
      const mesh = meshRef.m_FileID ? null : serialized.get(meshRef.m_PathID);
      if (mesh && mesh.classId !== 43) continue;   // a Mesh pointer must name a Mesh in this file
      const materialId = serialized.read(renderer).m_Materials[0]?.m_PathID;
      const texture = materialId ? await textureOf(materialId) : null;
      if (mesh && texture) {
        const data = decodeMesh(serialized.read(mesh));
        if (data.positions && data.uvs) {
          const positions = toThree(data.positions, world);
          for (const sub of data.submeshes) {
            parts.push({ name: go.m_Name, positions, uvs: data.uvs, indices: flip(sub.indices), texture,
              tint: tintOf(materialId) });
          }
        }
      }
    }
    const system = components.find((o) => classOf(o) === CLASS.ParticleSystem);
    if (system) {
      const particles = serialized.read(system);
      const rendererObj = components.find((o) => classOf(o) === CLASS.ParticleSystemRenderer);
      const materialId = rendererObj ? serialized.read(rendererObj).m_Materials[0]?.m_PathID : 0;
      if (particles.InitialModule.enabled) {
        const position = [world[12], world[13], -world[14]];
        emitters.push(emitterFrom(particles, position, materialId ? await textureOf(materialId) : null, go.m_Name));
      }
    }
  }

  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (const part of parts) for (let i = 0; i < part.positions.length; i += 3) for (let k = 0; k < 3; k++) {
    lo[k] = Math.min(lo[k], part.positions[i + k]);
    hi[k] = Math.max(hi[k], part.positions[i + k]);
  }
  for (const e of emitters) for (let k = 0; k < 3; k++) {
    lo[k] = Math.min(lo[k], e.position[k]);
    hi[k] = Math.max(hi[k], e.position[k]);
  }
  return { name: prefabName, parts, emitters, bounds: [lo, hi] };
}
