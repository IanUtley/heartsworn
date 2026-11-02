// Unity serialized files (the CAB inside a bundle): the type table, the object
// table, and a generic TypeTree reader that turns any object into plain JS.
// Bundles built for the 5.6 client carry their TypeTrees, so no per-class
// layouts are needed.

import { COMMON_STRINGS } from "./common_strings.js";

const COMMON = new Map(COMMON_STRINGS);

const SCALARS = {
  bool: [1, (c) => c.u8() !== 0],
  SInt8: [1, (c) => c.i8()], UInt8: [1, (c) => c.u8()], char: [1, (c) => c.u8()],
  short: [2, (c) => c.i16()], SInt16: [2, (c) => c.i16()],
  UInt16: [2, (c) => c.u16()], "unsigned short": [2, (c) => c.u16()],
  int: [4, (c) => c.i32()], SInt32: [4, (c) => c.i32()],
  UInt32: [4, (c) => c.u32()], "unsigned int": [4, (c) => c.u32()],
  float: [4, (c) => c.f32()], double: [8, (c) => c.f64()],
  "long long": [8, (c) => c.i64()], SInt64: [8, (c) => c.i64()],
  UInt64: [8, (c) => c.u64()], "unsigned long long": [8, (c) => c.u64()],
};

const ALIGN_FLAG = 0x4000;
const aligned = (node) => (node.meta & ALIGN_FLAG) !== 0;

class Cursor {
  constructor(bytes, le) {
    this.bytes = bytes;
    this.view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    this.le = le;
    this.pos = 0;
  }
  u8() { return this.bytes[this.pos++]; }
  i8() { return this.view.getInt8(this.pos++); }
  i16() { const v = this.view.getInt16(this.pos, this.le); this.pos += 2; return v; }
  u16() { const v = this.view.getUint16(this.pos, this.le); this.pos += 2; return v; }
  i32() { const v = this.view.getInt32(this.pos, this.le); this.pos += 4; return v; }
  u32() { const v = this.view.getUint32(this.pos, this.le); this.pos += 4; return v; }
  i64() { const v = Number(this.view.getBigInt64(this.pos, this.le)); this.pos += 8; return v; }
  u64() { const v = Number(this.view.getBigUint64(this.pos, this.le)); this.pos += 8; return v; }
  f32() { const v = this.view.getFloat32(this.pos, this.le); this.pos += 4; return v; }
  f64() { const v = this.view.getFloat64(this.pos, this.le); this.pos += 8; return v; }
  align(to = 4) { this.pos = Math.ceil(this.pos / to) * to; }
  bytesOf(n) { const v = this.bytes.subarray(this.pos, this.pos + n); this.pos += n; return v; }
  cstring() {
    let end = this.pos;
    while (this.bytes[end] !== 0) end++;
    const text = new TextDecoder().decode(this.bytes.subarray(this.pos, end));
    this.pos = end + 1;
    return text;
  }
  string() {
    const n = this.i32();
    const text = new TextDecoder().decode(this.bytes.subarray(this.pos, this.pos + n));
    this.pos += n;
    this.align(4);
    return text;
  }
}

// TypeTree blob (format 10 and 12+): node records plus a shared string table.
function readTypeTreeBlob(c, version) {
  const count = c.i32();
  const bufferSize = c.i32();
  const stride = version >= 19 ? 32 : 24;
  const records = c.pos;
  const strings = records + count * stride;
  c.pos = strings + bufferSize;
  const name = (value) => {
    if ((value & 0x80000000) === 0) {
      const at = c.pos;
      c.pos = strings + value;
      const text = c.cstring();
      c.pos = at;
      return text;
    }
    const offset = value & 0x7fffffff;
    return COMMON.get(offset) ?? String(offset);
  };
  const view = c.view;
  const le = c.le;
  const nodes = [];
  for (let i = 0; i < count; i++) {
    const p = records + i * stride;
    nodes.push({
      level: view.getUint8(p + 2),
      type: name(view.getUint32(p + 4, le)),
      name: name(view.getUint32(p + 8, le)),
      meta: view.getInt32(p + 20, le),
      children: [],
    });
  }
  const stack = [];
  for (const node of nodes) {
    while (stack.length && stack[stack.length - 1].level >= node.level) stack.pop();
    if (stack.length) stack[stack.length - 1].children.push(node);
    stack.push(node);
  }
  return nodes[0];
}

function readArray(sub, size, c) {
  if (sub.type in SCALARS) {
    const [width, read] = SCALARS[sub.type];
    let out;
    if (sub.type === "UInt8" || sub.type === "char") {
      out = c.bytesOf(size);
    } else {
      out = new Array(size);
      for (let i = 0; i < size; i++) out[i] = read(c);
    }
    if (aligned(sub)) c.align(4);
    return out;
  }
  if (sub.type === "string") {
    const out = new Array(size);
    for (let i = 0; i < size; i++) out[i] = c.string();
    return out;
  }
  const out = new Array(size);
  for (let i = 0; i < size; i++) out[i] = readValue(sub, c);
  return out;
}

function readValue(node, c) {
  let value;
  let align = aligned(node);
  if (node.type in SCALARS) {
    value = SCALARS[node.type][1](c);
  } else if (node.type === "string") {
    value = c.string();
  } else if (node.type === "TypelessData") {
    // Raw bytes: an int length, then the data, aligned like the other arrays.
    value = c.bytesOf(c.i32());
    align = true;
  } else if (node.children.length && node.children[0].type === "Array") {
    const array = node.children[0];
    if (aligned(array)) align = true;
    const size = c.i32();
    if (size < 0) throw new Error(`negative array length in ${node.name}`);
    value = readArray(array.children[1], size, c);
  } else {
    value = {};
    for (const child of node.children) value[child.name] = readValue(child, c);
  }
  if (align) c.align(4);
  return value;
}

// Parses a serialized file (version 9 to 21). Returns the header facts and the
// object table, plus read(object) which decodes one object with its TypeTree.
// ``layouts`` (optional) holds the engine's type trees by class ID, for files stored without their own (resources.assets).
export function parseSerializedFile(bytes, layouts = null) {
  const header = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const version = header.getUint32(8);
  const dataOffset = header.getUint32(12);
  if (version < 9 || version >= 22) throw new Error(`unsupported serialized file version ${version}`);
  const bigEndian = bytes[16] !== 0;
  const c = new Cursor(bytes, !bigEndian);
  c.pos = 20;
  const unityVersion = version >= 7 ? c.cstring() : "";
  if (version >= 8) c.i32();                                      // target platform
  const enableTypeTree = version >= 13 ? c.u8() !== 0 : true;

  const types = [];
  const typeCount = c.i32();
  for (let i = 0; i < typeCount; i++) {
    const classId = c.i32();
    if (version >= 16) c.u8();                                    // stripped
    if (version >= 17) c.i16();                                   // script index
    if (version >= 13) {
      if ((version < 16 && classId < 0) || (version >= 16 && classId === 114)) c.pos += 16;
      c.pos += 16;                                                 // old type hash
    }
    let node = null;
    if (enableTypeTree) {
      if (version < 12 && version !== 10) throw new Error("legacy TypeTree layout is not supported");
      node = readTypeTreeBlob(c, version);
    }
    types.push({ classId, node });
  }

  let bigIds = false;
  if (version >= 7 && version < 14) bigIds = c.i32() !== 0;
  const objects = [];
  const byId = new Map();
  const objectCount = c.i32();
  for (let i = 0; i < objectCount; i++) {
    let pathId;
    if (bigIds) pathId = c.i64();
    else if (version < 14) pathId = c.i32();
    else { c.align(4); pathId = c.i64(); }
    const byteStart = c.u32() + dataOffset;
    const byteSize = c.u32();
    const typeId = c.i32();
    let type;
    let classId;
    if (version < 16) {
      classId = c.u16();
      type = types.find((t) => t.classId === typeId);
    } else {
      type = types[typeId];
      classId = type.classId;
    }
    if (version >= 11 && version < 17) c.i16();
    if (version === 15 || version === 16) c.u8();
    const object = { pathId, byteStart, byteSize, classId,
      node: (type && type.node) || (layouts && layouts[String(classId)]) || null };
    objects.push(object);
    byId.set(pathId, object);
  }

  if (version >= 11) {
    const scriptCount = c.i32();
    for (let i = 0; i < scriptCount; i++) { c.i32(); c.align(4); c.i64(); }
  }
  const externals = [];
  const externalCount = c.i32();
  for (let i = 0; i < externalCount; i++) {
    if (version >= 6) c.cstring();
    if (version >= 5) { c.pos += 16; c.i32(); }
    externals.push(c.cstring());
  }

  return {
    version,
    unityVersion,
    objects,
    externals,
    // The object table entry for an id, or undefined.
    get: (pathId) => byId.get(pathId),
    // Decodes an object's fields. Pointer fields are { m_FileID, m_PathID }.
    read(object) {
      if (!object.node) throw new Error(`object ${object.pathId} has no TypeTree`);
      const reader = new Cursor(bytes, !bigEndian);
      reader.pos = object.byteStart;
      return readValue(object.node, reader);
    },
  };
}
