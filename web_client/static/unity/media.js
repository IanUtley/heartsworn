// Turns decoded Unity objects into pixels, triangles and audio bytes. Input is
// the plain object from serialized.js, so these functions only read fields.

const FLOAT_SIZE = [4, 2, 1, 1, 2, 2, 1, 1, 2, 2, 4, 4];   // Unity VertexFormat sizes

// Decodes Texture2D mip 0 to RGBA8 (top row first). Returns null for formats
// the browser does not decode, such as crunched and PVRTC/ETC textures.
export function decodeTexture(texture, data = texture["image data"]) {
  const width = texture.m_Width, height = texture.m_Height;
  const format = texture.m_TextureFormat;
  const rgba = decodeRows(format, data, width, height);
  if (!rgba) return null;
  // Unity stores rows bottom-up, so the first stored row is the image's last.
  const row = width * 4;
  const top = new Uint8ClampedArray(rgba.length);
  for (let y = 0; y < height; y++) top.set(rgba.subarray((height - 1 - y) * row, (height - y) * row), y * row);
  return { width, height, rgba: top };
}

function decodeRows(format, data, width, height) {
  if (format === 10) return decodeDxt(data, width, height, false);
  if (format === 12) return decodeDxt(data, width, height, true);
  const out = new Uint8ClampedArray(width * height * 4);
  const n = width * height;
  switch (format) {
    case 1:   // Alpha8
      for (let i = 0; i < n; i++) out.set([255, 255, 255, data[i]], i * 4);
      break;
    case 3:   // RGB24
      for (let i = 0; i < n; i++) out.set([data[i * 3], data[i * 3 + 1], data[i * 3 + 2], 255], i * 4);
      break;
    case 4:   // RGBA32
      out.set(data.subarray(0, n * 4));
      break;
    case 5:   // ARGB32 (stored A, R, G, B)
      for (let i = 0; i < n; i++) out.set([data[i * 4 + 1], data[i * 4 + 2], data[i * 4 + 3], data[i * 4]], i * 4);
      break;
    case 7:   // RGB565
      for (let i = 0; i < n; i++) {
        const v = data[i * 2] | (data[i * 2 + 1] << 8);
        out.set([expand5((v >> 11) & 31), expand6((v >> 5) & 63), expand5(v & 31), 255], i * 4);
      }
      break;
    case 14:  // BGRA32
      for (let i = 0; i < n; i++) out.set([data[i * 4 + 2], data[i * 4 + 1], data[i * 4], data[i * 4 + 3]], i * 4);
      break;
    default:
      return null;
  }
  return out;
}

const expand5 = (v) => (v << 3) | (v >> 2);
const expand6 = (v) => (v << 2) | (v >> 4);

function rgb565(c) {
  return [expand5((c >> 11) & 31), expand6((c >> 5) & 63), expand5(c & 31)];
}

// BC1 (DXT1) and BC3 (DXT5) block decoding over a 4x4 grid of blocks.
function decodeDxt(data, width, height, alpha) {
  const out = new Uint8ClampedArray(width * height * 4);
  const blockBytes = alpha ? 16 : 8;
  const palette = new Array(4);
  const alphas = new Array(8);
  let at = 0;
  for (let by = 0; by < height; by += 4) {
    for (let bx = 0; bx < width; bx += 4, at += blockBytes) {
      const colour = alpha ? at + 8 : at;
      const c0 = data[colour] | (data[colour + 1] << 8);
      const c1 = data[colour + 2] | (data[colour + 3] << 8);
      const bits = (data[colour + 4] | (data[colour + 5] << 8) | (data[colour + 6] << 16) | (data[colour + 7] << 24)) >>> 0;
      const a = rgb565(c0), b = rgb565(c1);
      palette[0] = [...a, 255];
      palette[1] = [...b, 255];
      const opaque = c0 > c1 || alpha;
      if (opaque) {
        palette[2] = [0, 1, 2].map((k) => Math.floor((2 * a[k] + b[k]) / 3)).concat(255);
        palette[3] = [0, 1, 2].map((k) => Math.floor((a[k] + 2 * b[k]) / 3)).concat(255);
      } else {
        palette[2] = [0, 1, 2].map((k) => Math.floor((a[k] + b[k]) / 2)).concat(255);
        palette[3] = [0, 0, 0, 0];
      }

      let alphaBits = 0;
      if (alpha) {
        const a0 = data[at], a1 = data[at + 1];
        alphas[0] = a0;
        alphas[1] = a1;
        if (a0 > a1) {
          for (let k = 1; k <= 6; k++) alphas[k + 1] = Math.floor(((7 - k) * a0 + k * a1) / 7);
        } else {
          for (let k = 1; k <= 4; k++) alphas[k + 1] = Math.floor(((5 - k) * a0 + k * a1) / 5);
          alphas[6] = 0;
          alphas[7] = 255;
        }
        // Six index bytes: 48 bits, three per texel, least significant first.
        alphaBits = data[at + 2] + data[at + 3] * 2 ** 8 + data[at + 4] * 2 ** 16 +
          data[at + 5] * 2 ** 24 + data[at + 6] * 2 ** 32 + data[at + 7] * 2 ** 40;
      }

      for (let py = 0; py < 4; py++) {
        const y = by + py;
        if (y >= height) break;
        for (let px = 0; px < 4; px++) {
          const x = bx + px;
          if (x >= width) continue;
          const i = py * 4 + px;
          const pixel = palette[(bits >>> (2 * i)) & 3];
          const o = (y * width + x) * 4;
          out[o] = pixel[0];
          out[o + 1] = pixel[1];
          out[o + 2] = pixel[2];
          out[o + 3] = alpha ? alphas[Math.floor(alphaBits / 2 ** (3 * i)) % 8] : pixel[3];
        }
      }
    }
  }
  return out;
}

// Reads one vertex attribute component as a float.
function component(view, at, format) {
  switch (format) {
    case 0: return view.getFloat32(at, true);
    case 1: return float16(view.getUint16(at, true));
    case 2: return view.getUint8(at) / 255;
    case 3: return Math.max(view.getInt8(at) / 127, -1);
    case 4: return view.getUint16(at, true) / 65535;
    case 5: return Math.max(view.getInt16(at, true) / 32767, -1);
    case 6: return view.getUint8(at);
    case 7: return view.getInt8(at);
    case 8: return view.getUint16(at, true);
    case 9: return view.getInt16(at, true);
    case 10: return view.getUint32(at, true);
    case 11: return view.getInt32(at, true);
    default: throw new Error(`vertex format ${format} is not supported`);
  }
}

function float16(h) {
  const sign = h & 0x8000 ? -1 : 1, exp = (h >> 10) & 31, frac = h & 1023;
  if (exp === 0) return sign * 2 ** -14 * (frac / 1024);
  if (exp === 31) return frac ? NaN : sign * Infinity;
  return sign * 2 ** (exp - 15) * (1 + frac / 1024);
}

// Decodes an uncompressed Mesh into attribute arrays and one index list per
// submesh. Positions and normals stay in Unity's left-handed space; the caller
// converts them. Compressed meshes are reported as unsupported.
// Unity's PackedBitVector: values of m_BitSize bits, packed end to end (the bit order of UnityPy's unpack_ints).
export function unpackInts(packed, start = 0, count = packed.m_NumItems) {
  const bits = packed.m_BitSize, data = packed.m_Data;
  const out = new Array(count);
  let bitPos = bits * start;
  let indexPos = Math.floor(bitPos / 8);
  bitPos %= 8;
  for (let i = 0; i < count; i++) {
    let value = 0, got = 0;
    while (got < bits) {
      value += (data[indexPos] >> bitPos) * 2 ** got;
      const take = Math.min(bits - got, 8 - bitPos);
      bitPos += take;
      got += take;
      if (bitPos === 8) { indexPos++; bitPos = 0; }
    }
    out[i] = value % 2 ** bits;
  }
  return out;
}

// The quantized floats of a PackedBitVector, scaled from m_Start over m_Range.
export function unpackFloats(packed, start = 0, count = packed.m_NumItems) {
  const out = new Float32Array(count);
  if (!packed.m_BitSize) return out.fill(packed.m_Start);
  const scale = packed.m_Range / (2 ** packed.m_BitSize - 1);
  const quantized = unpackInts(packed, start, count);
  for (let i = 0; i < count; i++) out[i] = quantized[i] * scale + packed.m_Start;
  return out;
}

// A compressed mesh (the m_CompressedMesh of a Mesh stored without its vertex buffer): positions, the first UV set and
// the triangles per submesh, in the same shape decodeMesh returns. Normals are not read: the background does not light.
export function decodeCompressedMesh(mesh) {
  const cm = mesh.m_CompressedMesh;
  const count = cm.m_Vertices.m_NumItems / 3;
  const positions = unpackFloats(cm.m_Vertices, 0, cm.m_Vertices.m_NumItems);
  let uvs = null;
  if (cm.m_UV && cm.m_UV.m_NumItems > 0) {
    // m_UVInfo packs four bits per channel: bit 2 says the channel exists, bits 0-1 give its dimension minus one.
    const info = cm.m_UVInfo || 0;
    const dim = info && (info & 4) ? 1 + (info & 3) : 2;
    const raw = unpackFloats(cm.m_UV, 0, count * dim);
    uvs = new Float32Array(count * 2);
    for (let i = 0; i < count; i++) {
      uvs[i * 2] = raw[i * dim];
      uvs[i * 2 + 1] = dim > 1 ? raw[i * dim + 1] : 0;
    }
  }
  const triangles = cm.m_Triangles && cm.m_Triangles.m_NumItems > 0 ? unpackInts(cm.m_Triangles) : [];
  const perIndex = mesh.m_IndexFormat === 1 ? 4 : 2;   // m_IndexFormat: 0 = 16-bit, 1 = 32-bit
  const submeshes = mesh.m_SubMeshes.map((sub) => {
    const first = sub.firstByte / perIndex;
    return { indices: Uint32Array.from(triangles.slice(first, first + sub.indexCount)), topology: sub.topology };
  });
  return { positions, normals: null, uvs, submeshes };
}

export function decodeMesh(mesh) {
  const cm = mesh.m_CompressedMesh;
  if (cm && cm.m_Vertices && cm.m_Vertices.m_NumItems > 0 && (!mesh.m_VertexData || !mesh.m_VertexData.m_VertexCount))
    return decodeCompressedMesh(mesh);
  if (mesh.m_MeshCompression !== 0) throw new Error("compressed meshes are not supported");
  const vertexData = mesh.m_VertexData;
  const count = vertexData.m_VertexCount;
  const channels = vertexData.m_Channels.map((ch) => ({ ...ch }));
  const streamStride = [];
  for (const ch of channels) {
    if (!ch.dimension) continue;
    streamStride[ch.stream] = (streamStride[ch.stream] || 0) + ch.dimension * FLOAT_SIZE[ch.format];
  }
  const streamStart = [];
  let start = 0;
  for (let s = 0; s < streamStride.length; s++) {
    streamStart[s] = start;
    start += count * (streamStride[s] || 0);
  }
  const data = vertexData.m_DataSize;
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);

  // Attribute lists by Unity channel: 0 vertex, 1 normal, 3 texcoord0.
  const attribute = (index, dimension) => {
    const ch = channels[index];
    if (!ch || !ch.dimension || ch.dimension < dimension) return null;
    const size = FLOAT_SIZE[ch.format];
    const out = new Float32Array(count * dimension);
    for (let v = 0; v < count; v++) {
      const base = streamStart[ch.stream] + v * streamStride[ch.stream] + ch.offset;
      for (let k = 0; k < dimension; k++) out[v * dimension + k] = component(view, base + k * size, ch.format);
    }
    return out;
  };

  const indexBytes = mesh.m_IndexBuffer;
  const indexView = new DataView(indexBytes.buffer, indexBytes.byteOffset, indexBytes.byteLength);
  const submeshes = mesh.m_SubMeshes.map((sub) => {
    const indices = new Uint32Array(sub.indexCount);
    for (let i = 0; i < sub.indexCount; i++) indices[i] = indexView.getUint16(sub.firstByte + i * 2, true);
    return { indices, topology: sub.topology };
  });

  return {
    positions: attribute(0, 3),
    normals: attribute(1, 3),
    uvs: attribute(3, 2),
    submeshes,
  };
}

// The bundle resource a clip's samples live in: "archive:/<bundle>/<file>" or a
// sibling file name. Returns { file, offset, size } for the given clip.
export function clipLocation(clip) {
  const resource = clip.m_Resource;
  return {
    file: resource.m_Source.split("/").pop(),
    offset: resource.m_Offset,
    size: resource.m_Size,
    format: clip.m_CompressionFormat,   // 1 is Ogg Vorbis
  };
}
