// Byte sources and the Unity containers the browser reads: LZ4 and LZMA block
// data, UnityFS asset bundles (.ab) and HARC archives, which wrap bundles.
//
// A source is { size, read(offset, length) -> Promise<Uint8Array> }. Browser
// files and folder entries fit that shape, so the same readers run in the
// page and under Node for checks.

import { lzmaDecompress } from "./lzma.js";

export function sliceSource(source, offset, size) {
  return { size, read: (start, length) => source.read(offset + start, length) };
}

// LZ4 block format (no frame header); UnityFS stores its blocks this way.
export function lz4Decompress(src, size) {
  const out = new Uint8Array(size);
  let s = 0, d = 0;
  while (s < src.length && d < size) {
    const token = src[s++];
    let literals = token >> 4;
    if (literals === 15) {
      let b;
      do { b = src[s++]; literals += b; } while (b === 255);
    }
    out.set(src.subarray(s, s + literals), d);
    s += literals;
    d += literals;
    if (s >= src.length) break;
    const offset = src[s] | (src[s + 1] << 8);
    s += 2;
    let length = token & 15;
    if (length === 15) {
      let b;
      do { b = src[s++]; length += b; } while (b === 255);
    }
    length += 4;
    let from = d - offset;
    for (let i = 0; i < length; i++) out[d++] = out[from++];
  }
  if (d !== size) throw new Error(`LZ4 block decoded to ${d} bytes, expected ${size}`);
  return out;
}

const COMPRESSION_MASK = 0x3f;
const BLOCKS_INFO_AT_END = 0x80;
const BLOCK_INFO_PADDING_AT_START = 0x200;

function decodeBlock(bytes, flags, size) {
  const kind = flags & COMPRESSION_MASK;
  if (kind === 0) return bytes;
  if (kind === 1) return lzmaDecompress(bytes, size);
  if (kind === 2 || kind === 3) return lz4Decompress(bytes, size);
  throw new Error(`bundle block compression ${kind} is not supported in the browser`);
}

function cstring(bytes, pos) {
  let end = pos;
  while (bytes[end] !== 0) end++;
  return { text: new TextDecoder().decode(bytes.subarray(pos, end)), next: end + 1 };
}

function alignUp(value, to) {
  return Math.ceil(value / to) * to;
}

// Opens a UnityFS bundle. Returns { names, has(name), read(name), readRange(name, offset, length) }
// where names are the bundle's files (the serialized CAB and its .resource streams).
export async function openBundle(source) {
  const head = await source.read(0, Math.min(source.size, 1024));
  const view = new DataView(head.buffer, head.byteOffset, head.byteLength);
  const signature = cstring(head, 0);
  if (signature.text !== "UnityFS") throw new Error(`not a UnityFS bundle: ${signature.text}`);
  let pos = signature.next;
  const version = view.getUint32(pos);
  pos += 4;
  pos = cstring(head, pos).next;   // player version
  pos = cstring(head, pos).next;   // engine version
  pos += 8;                        // total file size
  const infoSize = view.getUint32(pos);
  const infoUncompressed = view.getUint32(pos + 4);
  const flags = view.getUint32(pos + 8);
  pos += 12;
  if (version >= 7) pos = alignUp(pos, 16);

  const infoAt = flags & BLOCKS_INFO_AT_END ? source.size - infoSize : pos;
  const info = decodeBlock(await source.read(infoAt, infoSize), flags, infoUncompressed);
  const infoView = new DataView(info.buffer, info.byteOffset, info.byteLength);
  let ip = 16;                     // data hash
  const blockCount = infoView.getInt32(ip);
  ip += 4;
  let dataAt = flags & BLOCKS_INFO_AT_END ? pos : pos + infoSize;
  if (flags & BLOCK_INFO_PADDING_AT_START) dataAt = alignUp(dataAt, 16);
  const blocks = [];
  let unpacked = 0, packed = dataAt;
  for (let i = 0; i < blockCount; i++) {
    const size = infoView.getUint32(ip);
    const packedSize = infoView.getUint32(ip + 4);
    const blockFlags = infoView.getUint16(ip + 8);
    ip += 10;
    blocks.push({ start: unpacked, size, packed, packedSize, flags: blockFlags });
    unpacked += size;
    packed += packedSize;
  }
  const nodeCount = infoView.getInt32(ip);
  ip += 4;
  const nodes = new Map();
  for (let i = 0; i < nodeCount; i++) {
    const offset = Number(infoView.getBigInt64(ip));
    const size = Number(infoView.getBigInt64(ip + 8));
    ip += 16 + 4;
    const name = cstring(info, ip);
    ip = name.next;
    nodes.set(name.text, { offset, size });
  }

  const cache = new Map();
  async function block(index) {
    let bytes = cache.get(index);
    if (!bytes) {
      const b = blocks[index];
      bytes = decodeBlock(await source.read(b.packed, b.packedSize), b.flags, b.size);
      cache.set(index, bytes);
      if (cache.size > 16) cache.delete(cache.keys().next().value);
    }
    return bytes;
  }

  async function readRange(offset, length) {
    const out = new Uint8Array(length);
    let done = 0, index = blocks.findIndex((b) => offset < b.start + b.size);
    while (done < length) {
      const b = blocks[index];
      const bytes = await block(index);
      const from = offset + done - b.start;
      const n = Math.min(b.size - from, length - done);
      out.set(bytes.subarray(from, from + n), done);
      done += n;
      index++;
    }
    return out;
  }

  return {
    version,
    names: [...nodes.keys()],
    has: (name) => nodes.has(name),
    read: (name) => {
      const node = nodes.get(name);
      if (!node) throw new Error(`bundle has no ${name}`);
      return readRange(node.offset, node.size);
    },
    readRange: (name, offset, length) => {
      const node = nodes.get(name);
      if (!node) throw new Error(`bundle has no ${name}`);
      return readRange(node.offset + offset, length);
    },
  };
}

// Opens a HARC archive: "HARC", "offset,size,path" lines, a blank line, then
// the item data. Items are keyed by file stem, so "./a0000001.ab" is "a0000001".
export async function openHarc(source) {
  let header = "";
  let end = -1;
  for (let want = 1 << 16; end < 0; want *= 2) {
    const bytes = await source.read(0, Math.min(source.size, want));
    header = new TextDecoder("latin1").decode(bytes);
    end = header.indexOf("\r\n\r\n");
    if (end < 0 && bytes.length >= source.size) throw new Error("HARC header is truncated");
  }
  if (!header.startsWith("HARC\r\n")) throw new Error("not a HARC archive");
  const dataAt = end + 4;
  const items = new Map();
  for (const line of header.slice(6, end).split("\r\n")) {
    if (!line) continue;
    const [offset, size, path] = line.split(",", 3);
    const stem = path.split("/").pop().replace(/\.[^.]*$/, "");
    items.set(stem, { offset: dataAt + Number(offset), size: Number(size) });
  }
  return {
    items,
    item: (stem) => {
      const entry = items.get(stem);
      return entry && sliceSource(source, entry.offset, entry.size);
    },
  };
}
