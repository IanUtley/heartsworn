// The Hex client install, read in place: card art, card-play sounds and
// battleboards are decoded from the client's own asset bundles. Nothing is
// uploaded; the folder is read through a tree (see folderTree / fileListTree)
// and the results are kept in memory.

import { openBundle, openHarc } from "./reader.js";
import { parseSerializedFile } from "./serialized.js";
import { decodeTexture, decodeMesh, clipLocation } from "./media.js";
import { parseVorbisBank, vorbisBankToOgg } from "./fsb.js";
import { quatMatrix, mul, emitterFrom, backgroundLayers } from "./scene_layers.js";

const ART = /^a\d{7}$/;

// A byte source over a browser File, read on demand.
export function fileSource(file) {
  return { size: file.size, read: async (offset, length) => new Uint8Array(await file.slice(offset, offset + length).arrayBuffer()) };
}

// The Hex folder chosen with showDirectoryPicker().
export function folderTree(root) {
  const handleAt = async (path) => {
    let dir = root;
    for (const part of path.split("/").slice(0, -1)) dir = await dir.getDirectoryHandle(part);
    return { dir, name: path.split("/").pop() };
  };
  return {
    async file(path) {
      const { dir, name } = await handleAt(path);
      return fileSource(await (await dir.getFileHandle(name)).getFile());
    },
    async list(path) {
      let dir = root;
      for (const part of path.split("/").filter(Boolean)) dir = await dir.getDirectoryHandle(part);
      const names = [];
      for await (const [name, entry] of dir.entries()) if (entry.kind === "file") names.push(name);
      return names.sort();
    },
  };
}

// The Hex folder chosen through <input type="file" webkitdirectory>. The
// picked folder's own name is dropped from each relative path.
export function fileListTree(files) {
  const byPath = new Map();
  for (const file of files) {
    const parts = (file.webkitRelativePath || file.name).split("/").slice(1);
    byPath.set(parts.join("/"), file);
  }
  return {
    async file(path) {
      const file = byPath.get(path);
      if (!file) throw new Error(`${path} is not in the chosen folder`);
      return fileSource(file);
    },
    async list(path) {
      const prefix = path ? `${path.replace(/\/$/, "")}/` : "";
      return [...byPath.keys()].filter((p) => p.startsWith(prefix) && !p.slice(prefix.length).includes("/"))
        .map((p) => p.slice(prefix.length)).sort();
    },
  };
}

// Folder paths of a Hex install, used to report a bad choice clearly.
export class HexInstall {
  static async open(tree) {
    const cardsets = await tree.list("AssetBundles/cardsets").catch(() => []);
    if (!cardsets.some((name) => name.endsWith(".harc"))) {
      throw new Error("That folder has no AssetBundles/cardsets. Choose the HEX SHARDS OF FATE folder.");
    }
    return new HexInstall(tree);
  }

  constructor(tree) {
    this.tree = tree;
    this.artIndex = null;          // bundle stem -> the HARC archives holding it, in load order
    this.variants = new Map();     // art number -> stems like "a0007461_dr1"
    this.pixels = new Map();       // art number -> Promise<{width, height, rgba} | null>
    this.clips = new Map();        // "art|clip" -> Promise<Uint8Array | null>
    this.boards = new Map();       // board key -> Promise<board | null>
    this.soundIndex = null;        // shared clip name -> where its audio sits (see indexSounds)
  }

  // Indexes every card bundle once. A card's art is in several HARC archives
  // (the same card reprinted in different sets), so each stem keeps all of them.
  indexArt(onProgress) {
    if (!this.artIndex) {
      this.artIndex = (async () => {
        const index = new Map();
        const archives = (await this.tree.list("AssetBundles/cardsets")).filter((n) => n.endsWith(".harc"));
        for (const name of archives) {
          onProgress?.(`Reading ${name}`);
          const harc = await openHarc(await this.tree.file(`AssetBundles/cardsets/${name}`));
          for (const stem of harc.items.keys()) {
            const art = stem.slice(0, 8);
            if (!ART.test(art) || (stem.length > 8 && stem[8] !== "_")) continue;
            if (!index.has(stem)) index.set(stem, []);
            index.get(stem).push(harc);
            if (stem !== art) {
              if (!this.variants.has(art)) this.variants.set(art, []);
              this.variants.get(art).push(stem);
            }
          }
        }
        return index;
      })();
    }
    return this.artIndex;
  }

  // Each bundle under a stem, in load order, as { bundle, serialized }.
  async *bundlesFor(stem) {
    const index = await this.indexArt();
    for (const harc of index.get(stem) || []) {
      const bundle = await openBundle(harc.item(stem));
      yield { bundle, serialized: parseSerializedFile(await bundle.read(serializedName(bundle))) };
    }
  }

  // Card art as RGBA (top row first), or null when the client has none.
  artPixels(art) {
    if (!ART.test(art || "")) return Promise.resolve(null);
    if (!this.pixels.has(art)) {
      this.pixels.set(art, (async () => {
        for await (const found of this.bundlesFor(art)) {
          const texture = objectsOf(found.serialized, "Texture2D")[0];
          if (texture) return decodeTexture(found.serialized.read(texture));
        }
        console.warn(`Card art ${art}: no texture in the Hex folder's card bundles`);
        return null;
      })().catch((err) => {
        console.warn(`Card art ${art} could not be decoded`, err);
        return null;
      }));
    }
    return this.pixels.get(art);
  }

  // Bytes of a card-play sound. The clip sits in one of the card's own art
  // bundles (or a _dr variant of it); clipName is the gamedata file's basename.
  clipBytes(art, clipName) {
    const key = `${art}|${clipName}`;
    if (!this.clips.has(key)) {
      this.clips.set(key, (async () => {
        const stems = [art, ...(this.variants.get(art) || [])];
        for (const stem of stems) {
          for await (const found of this.bundlesFor(stem)) {
            const clip = objectsOf(found.serialized, "AudioClip")
              .map((o) => found.serialized.read(o)).find((c) => c.m_Name === clipName);
            if (clip) return this.clipSamples(found.bundle, clip);
          }
        }
        return null;
      })().catch(() => null));
    }
    return this.clips.get(key);
  }

  // The clip's encoded audio as a playable file: FMOD Vorbis banks are remuxed
  // to Ogg, and anything else is returned as stored.
  async clipSamples(bundle, clip) {
    const location = clipLocation(clip);
    const bytes = location.file.endsWith(".resource") && bundle.has(location.file)
      ? await bundle.readRange(location.file, location.offset, location.size)
      : await (await this.tree.file(`Hex_Data/${location.file}`)).read(location.offset, location.size);
    const bank = parseVorbisBank(bytes);
    return bank ? vorbisBankToOgg(bank, clip.m_Frequency) : bytes;
  }

  // Bytes of a shared sound clip (battle and UI effects such as Aud_FX_Main_Phase), as Ogg. The clips sit
  // in the Hex_Data sharedassets files, indexed once by name. Returns null for a clip this reader cannot decode.
  async soundBytes(name) {
    // A name can sit in more than one file; the first copy this reader can decode is used.
    for (const at of (await this.sounds()).get(name) || []) {
      const source = await this.tree.file(`Hex_Data/${at.source}`);
      const bank = parseVorbisBank(await source.read(at.offset, at.size));
      if (bank) return vorbisBankToOgg(bank, at.frequency);
    }
    return null;
  }

  // The clip a sound name plays: the name itself, or for a sound group (such as Aud_FX_Card_Draw, whose
  // variants are Aud_FX_Card_Draw_01 and so on) one variant picked at random. Null when there is none.
  async soundName(name) {
    const index = await this.sounds();
    if (index.has(name)) return name;
    const variants = [...index.keys()].filter((n) => n.startsWith(`${name}_`) && /^\d+$/.test(n.slice(name.length + 1)));
    return variants.length ? variants[Math.floor(Math.random() * variants.length)] : null;
  }

  sounds() {
    if (!this.soundIndex) this.soundIndex = this.indexSounds();
    return this.soundIndex;
  }

  // Every AudioClip in the sharedassets files, by name, as a list of its copies. These files keep no TypeTree, so each clip's
  // fields are read from its bytes (see audioClipFields).
  async indexSounds() {
    const index = new Map();
    const files = (await this.tree.list("Hex_Data")).filter((n) => /^sharedassets\d+\.assets$/.test(n));
    for (const file of files) {
      const source = await this.tree.file(`Hex_Data/${file}`);
      const bytes = await source.read(0, source.size);
      const serialized = parseSerializedFile(bytes);
      for (const object of serialized.objects.filter((o) => o.classId === CLASS_AUDIO_CLIP)) {
        const clip = audioClipFields(bytes, object);
        if (!index.has(clip.name)) index.set(clip.name, []);
        index.get(clip.name).push(clip);
      }
    }
    return index;
  }

  // The layers of a Background_* prefab (Settings > Background), read from resources.assets, which is parsed once.
  // resources.assets stores no type trees, so the engine layouts in typetrees.json stand in for them.
  background(prefabName) {
    if (!this.backgroundScenes) this.backgroundScenes = new Map();
    if (!this.backgroundScenes.has(prefabName)) {
      this.backgroundScenes.set(prefabName, (async () => {
        if (!this.resources) {
          this.resources = (async () => {
            const source = await this.tree.file("Hex_Data/resources.assets");
            const layouts = (await (await fetch("/static/unity/typetrees.json")).json()).classes;
            return parseSerializedFile(await source.read(0, source.size), layouts);
          })();
        }
        const readStream = async (path, offset, size) =>
          (await (await this.tree.file(`Hex_Data/${path}`)).read(offset, size));
        // External files the prefab refers to: "library/..." and "resources/..." are the built-in files in Hex_Data/Resources.
        const readFile = async (name) => {
          if (!this.externalFiles) this.externalFiles = new Map();
          if (!this.externalFiles.has(name)) {
            const base = name.replace(/^(library|resources)\//, "");
            const path = /^(library|resources)\//.test(name) ? `Hex_Data/Resources/${base}` : `Hex_Data/${name}`;
            this.externalFiles.set(name, (async () => {
              const source = await this.tree.file(path);
              const layouts = (await (await fetch("/static/unity/typetrees.json")).json()).classes;
              return parseSerializedFile(await source.read(0, source.size), layouts);
            })());
          }
          return this.externalFiles.get(name);
        };
        return backgroundLayers(await this.resources, prefabName, readStream, readFile);
      })().catch(() => null));
    }
    return this.backgroundScenes.get(prefabName);
  }

  // A battleboard: its textured meshes in Unity's world space, converted to
  // three.js axes (Unity is left-handed, so Z is mirrored and winding flips).
  // Battleboard keys: the AssetBundles/gameboards/<key>.ab files that hold a
  // playing surface (a GameObject named Gameboard*). Other bundles, such as the
  // deck dais, are props and are left out. Checked once, then cached.
  boardNames() {
    if (!this.boardList) {
      this.boardList = (async () => {
        const names = (await this.tree.list("AssetBundles/gameboards"))
          .filter((n) => n.endsWith(".ab")).map((n) => n.slice(0, -3)).sort();
        const boards = [];
        for (const key of names) {
          const bundle = await openBundle(await this.tree.file(`AssetBundles/gameboards/${key}.ab`));
          const serialized = parseSerializedFile(await bundle.read(serializedName(bundle)));
          const surface = objectsOf(serialized, "GameObject")
            .some((o) => serialized.read(o).m_Name.startsWith("Gameboard"));
          if (surface) boards.push(key);
        }
        return boards;
      })();
    }
    return this.boardList;
  }

  // The card face atlases (NGUI) the client builds its cards from, as
  // { Frames, Elements, Icons }: each { width, height, rgba, sprites }. They sit
  // in Hex_Data/resources.assets, which is stored without TypeTrees, so the few
  // fields needed are read by hand (Unity 5.6 layouts).
  cardChrome() {
    if (!this.chrome) this.chrome = readCardChrome(this.tree).catch(() => null);
    return this.chrome;
  }

  gameboard(key) {
    if (!this.boards.has(key)) {
      this.boards.set(key, (async () => {
        if (!/^[\w.-]+$/.test(key)) return null;
        const bundle = await openBundle(await this.tree.file(`AssetBundles/gameboards/${key}.ab`));
        const sf = parseSerializedFile(await bundle.read(serializedName(bundle)));
        return { ...boardFrom(sf, bundle), key };
      })().catch(() => null));
    }
    return this.boards.get(key);
  }
}

const CLASS_AUDIO_CLIP = 83;

// The fields of an AudioClip stored without a TypeTree (Unity 5.6): name, load type, channels, frequency,
// bits, length, two flags, then its resource as { m_Source, m_Offset, m_Size }.
function audioClipFields(bytes, object) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let at = object.byteStart;
  const text = () => {
    const length = view.getInt32(at, true);
    const value = new TextDecoder().decode(bytes.subarray(at + 4, at + 4 + length));
    at = (at + 4 + length + 3) & ~3;
    return value;
  };
  const name = text();
  const frequency = view.getInt32(at + 8, true);
  at += 32;   // load type, channels, frequency, bits, length, the two flags
  const source = text().split("/").pop();
  const offset = Number(view.getBigUint64(at, true));
  const size = Number(view.getBigUint64(at + 8, true));
  return { name, frequency, source, offset, size };
}

function serializedName(bundle) {
  return bundle.names.find((n) => n.startsWith("CAB-") && !n.endsWith(".resource"));
}

function objectsOf(serialized, type) {
  return serialized.objects.filter((o) => o.node && o.node.type === type);
}

// -- card face atlases -------------------------------------------------------

const CHROME_ATLASES = ["Frames", "Elements", "Icons"];
const CLASS_MATERIAL = 21, CLASS_TEXTURE = 28, CLASS_MONOBEHAVIOUR = 114;
// Bytes per pixel of the uncompressed formats media.js decodes; the DXT ones
// are sized per 4x4 block below.
const PIXEL_BYTES = { 1: 1, 3: 3, 4: 4, 5: 4, 7: 2, 14: 4 };

// Little-endian field reader for objects stored without a TypeTree.
class Fields {
  constructor(bytes) {
    this.bytes = bytes;
    this.view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    this.pos = 0;
  }
  i32() { const v = this.view.getInt32(this.pos, true); this.pos += 4; return v; }
  u32() { const v = this.view.getUint32(this.pos, true); this.pos += 4; return v; }
  u8() { return this.bytes[this.pos++]; }
  align() { this.pos = (this.pos + 3) & ~3; }
  string() {
    const n = this.i32();
    if (n < 0 || this.pos + n > this.bytes.length) throw new Error("not a string");
    const text = new TextDecoder().decode(this.bytes.subarray(this.pos, this.pos + n));
    this.pos += n;
    this.align();
    return text;
  }
  pptr() { const file = this.i32(); const id = Number(this.view.getBigInt64(this.pos, true)); this.pos += 8; return { file, id }; }
}

async function readCardChrome(tree) {
  const file = await tree.file("Hex_Data/resources.assets");
  const head = await file.read(0, 20);
  const dataOffset = new DataView(head.buffer, head.byteOffset, 20).getUint32(12);
  const serialized = parseSerializedFile(await file.read(0, dataOffset));
  const bytesOf = (o, limit = o.byteSize) => file.read(o.byteStart, Math.min(o.byteSize, limit));
  const nameOf = async (o) => { try { return new Fields(await bytesOf(o, 256)).string(); } catch { return ""; } };
  const ofClass = (id) => serialized.objects.filter((o) => o.classId === id);

  // Each atlas is a material and texture named CardTemplate_<atlas>; the
  // greyed "_g" copies are skipped by the exact name.
  const wanted = new Map(CHROME_ATLASES.map((atlas) => [`CardTemplate_${atlas}`, atlas]));
  const materials = new Map(), textures = new Map();
  for (const o of ofClass(CLASS_MATERIAL)) {
    const atlas = wanted.get(await nameOf(o));
    if (atlas) materials.set(o.pathId, atlas);
  }
  for (const o of ofClass(CLASS_TEXTURE)) {
    const atlas = wanted.get(await nameOf(o));
    if (atlas) textures.set(atlas, o);
  }

  // UIAtlas behaviours: the sprite list follows the material pointer.
  const sprites = new Map();
  for (const o of ofClass(CLASS_MONOBEHAVIOUR)) {
    if (sprites.size === materials.size) break;
    if (o.byteSize < 64 || o.byteSize > 1 << 16) continue;
    try {
      const f = new Fields(await bytesOf(o, 160));
      f.pptr(); f.u8(); f.align(); f.pptr(); f.string();
      const material = f.pptr();
      const atlas = material.file === 0 && materials.get(material.id);
      if (!atlas || sprites.has(atlas)) continue;
      sprites.set(atlas, atlasSprites(new Fields(await bytesOf(o)), f.pos));
    } catch { /* not a UIAtlas */ }
  }

  const out = {};
  for (const atlas of CHROME_ATLASES) {
    const texture = textures.get(atlas);
    if (!texture || !sprites.has(atlas)) return null;
    const pixels = await atlasPixels(tree, new Fields(await bytesOf(texture)));
    if (!pixels) return null;
    out[atlas] = { ...pixels, sprites: sprites.get(atlas) };
  }
  return out;
}

// UISpriteData list: name, rect, border, padding, rotated. Rects count from
// the atlas's top-left; a rotated sprite is stored a quarter turn clockwise.
// The padding is the
// trimmed margin of the sprite's original card-sized image, so it places the
// sprite on the card face.
function atlasSprites(f, at) {
  f.pos = at;
  const count = f.i32();
  const out = {};
  for (let i = 0; i < count; i++) {
    const name = f.string();
    const [x, y, w, h] = [f.i32(), f.i32(), f.i32(), f.i32()];
    const border = [f.i32(), f.i32(), f.i32(), f.i32()];           // 9-slice: left, right, top, bottom
    const [left, right, top, bottom] = [f.i32(), f.i32(), f.i32(), f.i32()];
    const rotated = f.u8() !== 0;
    f.align();
    out[name] = { x, y, w, h, left, right, top, bottom, rotated, border };
  }
  return out;
}

// Texture2D (5.6): header fields, inline image data, then the stream location.
async function atlasPixels(tree, f) {
  f.string();
  const texture = { m_Width: f.i32(), m_Height: f.i32() };
  const complete = f.i32();
  texture.m_TextureFormat = f.i32();
  f.i32(); f.u8(); f.u8(); f.align();                             // mips, readable flags
  f.pos += 4 * 8;                                                  // image count .. colour space
  const inline = f.i32();
  let data = f.bytes.subarray(f.pos, f.pos + inline);
  f.pos += inline; f.align();
  if (!inline) {
    const offset = f.u32(), size = f.u32(), path = f.string();
    const { m_Width: w, m_Height: h, m_TextureFormat: format } = texture;
    // Only mip 0 is decoded, so only its bytes are read.
    const mip0 = format === 10 ? w * h / 2 : format === 12 ? w * h : w * h * (PIXEL_BYTES[format] || 0);
    data = await (await tree.file(`Hex_Data/${path}`)).read(offset, Math.min(mip0 || complete, size));
  }
  return decodeTexture(texture, data);
}

// -- battleboard scene -------------------------------------------------------

// CRC32 (the zlib polynomial), the hash Unity's animation clips use for object
// paths and property names.
const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
function crc32(text) {
  let c = 0xffffffff;
  for (const byte of new TextEncoder().encode(text)) c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

const ACTIVE_CURVE = crc32("m_IsActive");
const GAMEOBJECT_BINDING = 1;

// Objects that an animation clip switches on and off (m_IsActive curves) are the
// board's transitional effects: the void portal's open, loop and close, and the
// intro. Their children go with them. Clip bindings hash each object's path, so the
// objects are matched here by the same hash. Returns a predicate for a GameObject id.
function animatedObjects(serialized, transformByGameObject, transformById) {
  const goByHash = new Map();
  for (const [goId, transform] of transformByGameObject) {
    const names = [];
    for (let t = transform, depth = 0; t && depth < 64; depth++) {
      names.unshift(serialized.read(serialized.get(t.m_GameObject.m_PathID)).m_Name);
      t = transformById.get(t.m_Father.m_PathID);
    }
    goByHash.set(crc32(names.join("/")), goId);
    // Some clips count paths from the board's root, leaving that root out.
    if (names.length > 1) goByHash.set(crc32(names.slice(1).join("/")), goId);
  }
  const hidden = new Set();
  for (const o of objectsOf(serialized, "AnimationClip")) {
    for (const binding of serialized.read(o).m_ClipBindingConstant.genericBindings) {
      if (binding.typeID === GAMEOBJECT_BINDING && binding.attribute === ACTIVE_CURVE && goByHash.has(binding.path)) {
        hidden.add(goByHash.get(binding.path));
      }
    }
  }
  return (goId) => {
    for (let t = transformByGameObject.get(goId), depth = 0; t && depth < 64; depth++) {
      if (hidden.has(t.m_GameObject.m_PathID)) return true;
      t = transformById.get(t.m_Father.m_PathID);
    }
    return false;
  };
}

function boardFrom(serialized, bundle) {
  const transformByGameObject = new Map();
  const transformById = new Map();
  for (const o of objectsOf(serialized, "Transform")) {
    const t = serialized.read(o);
    transformByGameObject.set(t.m_GameObject.m_PathID, t);
    transformById.set(o.pathId, t);
  }
  const goNamed = (goId) => {
    const go = serialized.get(goId) && serialized.read(serialized.get(goId));
    return go ? go.m_Name : null;
  };
  // Local-to-world matrix, walking up the Transform hierarchy.
  const worldOf = (transform, depth = 0) => {
    const local = quatMatrix(transform.m_LocalRotation, transform.m_LocalScale, transform.m_LocalPosition);
    const father = transformById.get(transform.m_Father.m_PathID);
    if (!father || depth > 64) return local;
    return mul(worldOf(father, depth + 1), local);
  };

  const texIds = new Map();
  const textures = new Map();
  // A material's tint and emissive colour (Unity _Color and _EmissiveColor).
  const colorsOf = (materialId) => {
    const material = serialized.get(materialId) && serialized.read(serialized.get(materialId));
    const find = (name, fallback) => {
      const entry = material && material.m_SavedProperties.m_Colors.find((e) => e.first === name);
      return entry ? [entry.second.r, entry.second.g, entry.second.b] : fallback;
    };
    // Glow materials (particle-style shaders) carry _TintColor and add light.
    const glow = !!(material && material.m_SavedProperties.m_Colors.some((e) => e.first === "_TintColor"));
    return { tint: find("_Color", [1, 1, 1]), emissive: find("_EmissiveColor", [0, 0, 0]),
      emission: find("_EmissionColor", [0, 0, 0]), glow: glow ? find("_TintColor", [1, 1, 1]) : null };
  };
  // A material's texture slot (_MainTex for the albedo, _ESRA for the emission
  // mask of the playing surface and daises).
  const textureOf = (materialId, slot = "_MainTex") => {
    const key = `${materialId}|${slot}`;
    if (!texIds.has(key)) {
      const material = serialized.get(materialId) && serialized.read(serialized.get(materialId));
      const env = material && material.m_SavedProperties.m_TexEnvs.find((e) => e.first === slot);
      texIds.set(key, env ? env.second.m_Texture.m_PathID : 0);
    }
    const texId = texIds.get(key);
    if (!texId) return null;
    if (!textures.has(texId)) {
      const texture = serialized.get(texId);
      textures.set(texId, texture ? decodeTexture(serialized.read(texture)) : null);
    }
    return textures.get(texId);
  };

  const meshes = new Map();
  const readMesh = (meshId) => {
    if (!meshes.has(meshId)) {
      const object = serialized.get(meshId);
      meshes.set(meshId, object ? decodeMesh(serialized.read(object)) : null);
    }
    return meshes.get(meshId);
  };

  const ancestorIn = (goId, test) => {
    for (let t = transformByGameObject.get(goId), depth = 0; t && depth < 64; depth++) {
      const id = t.m_GameObject.m_PathID;
      if (test(id)) return id;
      t = transformById.get(t.m_Father.m_PathID);
    }
    return null;
  };
  // The centre of a GameObject's own mesh, in three.js axes.
  const meshCentreOf = (goId) => {
    const filter = objectsOf(serialized, "MeshFilter").map((f) => serialized.read(f))
      .find((f) => f.m_GameObject.m_PathID === goId);
    const mesh = filter && readMesh(filter.m_Mesh.m_PathID);
    const transform = transformByGameObject.get(goId);
    if (!mesh || !mesh.positions || !transform) return null;
    const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < mesh.positions.length; i += 3) for (let k = 0; k < 3; k++) {
      lo[k] = Math.min(lo[k], mesh.positions[i + k]);
      hi[k] = Math.max(hi[k], mesh.positions[i + k]);
    }
    const c = [0, 1, 2].map((k) => (lo[k] + hi[k]) / 2);
    const w = worldOf(transform);
    return [w[0] * c[0] + w[4] * c[1] + w[8] * c[2] + w[12],
      w[1] * c[0] + w[5] * c[1] + w[9] * c[2] + w[13],
      -(w[2] * c[0] + w[6] * c[1] + w[10] * c[2] + w[14])];
  };
  // A deck anchor in three.js axes, measured from the origin of the board's geometry:
  // an anchor under an offset root (badlands' Gameboard root) shares that offset.
  const anchorAt = (goId) => {
    let top = transformByGameObject.get(goId);
    for (let depth = 0; depth < 64 && transformById.get(top.m_Father.m_PathID); depth++) {
      top = transformById.get(top.m_Father.m_PathID);
    }
    const w = worldOf(transformByGameObject.get(goId)), r = worldOf(top);
    return [w[12] - r[12], w[13] - r[13], -(w[14] - r[14])];
  };
  // The client puts each side's deck dais on its deck anchor (AnimGameBoard), parenting
  // the dais and keeping its local offsets. So the dais and everything under it (its
  // void portal, the fire ring and their particles) moves by the anchor. A board can
  // also place its own dais at the anchor: that copy is kept where it is, and the
  // authored copies of the side are left out.
  const DAIS_NAME = /^(DeckDais_.+_(Ally|Enemy)|(Ally|Enemy)DeckDais)$/;
  const DAIS_REACH = 20;            // a placed dais's mesh centre lies this near its anchor
  const deckShifts = new Map();     // dais GameObject id -> anchor position (three.js axes)
  const daisOwners = new Set();     // dais GameObject ids, placed or moved
  const leftOutDais = new Set();    // authored copies of a side that the board places itself
  const goIds = [...transformByGameObject.keys()];
  for (const side of ["Ally", "Enemy"]) {
    const anchorName = side === "Ally" ? "PlayerDeckAnchorTarget" : "OpponentDeckAnchorTarget";
    const anchorId = goIds.find((id) => goNamed(id) === anchorName);
    if (anchorId === undefined) continue;
    const anchor = anchorAt(anchorId);
    const candidates = goIds.filter((id) => DAIS_NAME.test(goNamed(id)) && goNamed(id).endsWith(side));
    const placed = candidates.filter((id) => {
      const c = meshCentreOf(id);
      return c && Math.hypot(c[0] - anchor[0], c[2] - anchor[2]) <= DAIS_REACH;
    });
    if (placed.length) {
      for (const id of placed) daisOwners.add(id);
      for (const id of candidates) if (!placed.includes(id)) leftOutDais.add(id);
    } else {
      for (const id of candidates) {
        deckShifts.set(id, anchor);
        daisOwners.add(id);
      }
    }
  }
  const deckShiftOf = (goId) => {
    const id = ancestorIn(goId, (x) => deckShifts.has(x));
    return id === null ? [0, 0, 0] : deckShifts.get(id);
  };
  const inactive = animatedObjects(serialized, transformByGameObject, transformById);
  const leftOut = (goId) => inactive(goId) || ancestorIn(goId, (x) => leftOutDais.has(x)) !== null;
  // The bundle also carries the imported models (MDL_*) the board prefab was built
  // from, at their import scale and offsets. Their renderers keep Unity's built-in
  // Default-Material; the placed instances have the board's materials (some kept in
  // a shared bundle, so a material missing from this one is not a model import).
  const modelImport = (renderer) => renderer.m_Materials.length > 0 && renderer.m_Materials.every((m) => {
    const material = !m.m_FileID && m.m_PathID && serialized.get(m.m_PathID);
    return !!material && serialized.read(material).m_Name === "Default-Material";
  });
  const meshOf = new Map();       // GameObject id -> mesh id
  for (const f of objectsOf(serialized, "MeshFilter")) {
    const filter = serialized.read(f);
    meshOf.set(filter.m_GameObject.m_PathID, String(filter.m_Mesh.m_PathID));
  }

  const parts = [];
  for (const o of objectsOf(serialized, "MeshRenderer")) {
    const renderer = serialized.read(o);
    const goId = renderer.m_GameObject.m_PathID;
    const go = serialized.read(serialized.get(goId));
    if (!go.m_IsActive || leftOut(goId)) continue;
    // Cutout meshes (PhaseLens_Cutout, PhaseLens_Cutout_Depth) are depth masks: they
    // hide what is behind them in the client, so they are not drawn as surfaces.
    if (/cutout/i.test(go.m_Name)) continue;
    const filter = objectsOf(serialized, "MeshFilter")
      .map((f) => serialized.read(f)).find((f) => f.m_GameObject.m_PathID === goId);
    const mesh = filter && readMesh(filter.m_Mesh.m_PathID);
    if (!mesh || !mesh.positions) continue;
    const transform = transformByGameObject.get(goId);
    if (!transform) continue;
    const world = worldOf(transform);
    const shift = deckShiftOf(goId);
    const xf = (v) => {
      const x = world[0] * v[0] + world[4] * v[1] + world[8] * v[2] + world[12];
      const y = world[1] * v[0] + world[5] * v[1] + world[9] * v[2] + world[13];
      const z = world[2] * v[0] + world[6] * v[1] + world[10] * v[2] + world[14];
      return [x + shift[0], y + shift[1], -z + shift[2]];
    };
    const nrm = (v) => {
      const x = world[0] * v[0] + world[4] * v[1] + world[8] * v[2];
      const y = world[1] * v[0] + world[5] * v[1] + world[9] * v[2];
      const z = world[2] * v[0] + world[6] * v[1] + world[10] * v[2];
      const len = Math.hypot(x, y, z) || 1;
      return [x / len, y / len, -z / len];
    };
    const positions = new Float32Array(mesh.positions.length);
    for (let i = 0; i < mesh.positions.length; i += 3) {
      positions.set(xf([mesh.positions[i], mesh.positions[i + 1], mesh.positions[i + 2]]), i);
    }
    let normals = null;
    if (mesh.normals) {
      normals = new Float32Array(mesh.normals.length);
      for (let i = 0; i < mesh.normals.length; i += 3) normals.set(nrm([mesh.normals[i], mesh.normals[i + 1], mesh.normals[i + 2]]), i);
    }
    const materialIds = renderer.m_Materials.map((m) => m.m_PathID);
    const partBox = [[Infinity, Infinity, Infinity], [-Infinity, -Infinity, -Infinity]];
    for (let i = 0; i < positions.length; i += 3) {
      for (let k = 0; k < 3; k++) {
        partBox[0][k] = Math.min(partBox[0][k], positions[i + k]);
        partBox[1][k] = Math.max(partBox[1][k], positions[i + k]);
      }
    }
    mesh.submeshes.forEach((sub, index) => {
      if (sub.topology !== 0) return;               // triangles only
      const indices = new Uint32Array(sub.indices.length);
      for (let i = 0; i < sub.indices.length; i += 3) {
        indices[i] = sub.indices[i];
        indices[i + 1] = sub.indices[i + 2];       // mirrored: reverse the winding
        indices[i + 2] = sub.indices[i + 1];
      }
      const materialId = materialIds[Math.min(index, materialIds.length - 1)];
      const colors = materialId ? colorsOf(materialId) : { tint: [1, 1, 1], emissive: [0, 0, 0], emission: [0, 0, 0], glow: null };
      // The emission mask is the _ESRA slot where the material has one (the frost
      // arena's rune lines), else its _EmissionMap, lit by the material's _EmissionColor.
      const emissiveTexture = materialId ? (textureOf(materialId, "_ESRA") || textureOf(materialId, "_EmissionMap")) : null;
      parts.push({ positions, normals, uvs: mesh.uvs, indices, texture: materialId ? textureOf(materialId) : null,
        emissiveTexture, emission: colors.emission,
        tint: colors.tint, emissive: colors.emissive, glow: colors.glow, name: go.m_Name, box: partBox,
        meshId: meshOf.get(goId), modelImport: modelImport(renderer),
        dais: daisOwners.has(goId) });
    });
  }

  // Import copies near the arena have no texture; the placed instances with the same
  // mesh, which may sit far away, carry the real one. Give each untextured part the
  // texture (and emission) of a textured part with the same name and vertex count.
  const copyOf = new Map();
  for (const part of parts) {
    const key = `${part.name}|${part.positions.length}`;
    if (part.texture && !copyOf.has(key)) copyOf.set(key, part);
  }
  for (const part of parts) {
    if (part.texture) continue;
    const source = copyOf.get(`${part.name}|${part.positions.length}`);
    if (!source) continue;
    part.texture = source.texture;
    part.emissiveTexture = source.emissiveTexture;
    part.emission = source.emission;
  }

  // Particle systems: each one's emission, shape, colour and texture, kept near
  // the arena like the meshes.
  const rendererOf = new Map();
  for (const o of objectsOf(serialized, "ParticleSystemRenderer")) {
    const renderer = serialized.read(o);
    rendererOf.set(renderer.m_GameObject.m_PathID, renderer);
  }
  const emitters = [];
  for (const o of objectsOf(serialized, "ParticleSystem")) {
    const system = serialized.read(o);
    const goId = system.m_GameObject.m_PathID;
    const go = serialized.read(serialized.get(goId));
    const transform = transformByGameObject.get(goId);
    if (!go.m_IsActive || leftOut(goId) || !transform || !system.InitialModule.enabled) continue;
    const world = worldOf(transform);
    const shift = deckShiftOf(goId);
    const position = [world[12] + shift[0], world[13] + shift[1], -world[14] + shift[2]];
    if (Math.hypot(position[0], position[2]) > ARENA_MAX_CENTRE) continue;
    const renderer = rendererOf.get(goId);
    const materialId = renderer && renderer.m_Materials[0] ? renderer.m_Materials[0].m_PathID : 0;
    emitters.push(emitterFrom(system, position, materialId ? textureOf(materialId) : null, go.m_Name));
  }

  // The scene also holds far-away copies of the props and oversized set pieces
  // (the skull, the stairs). Keep the parts that sit around the arena.
  let near = parts.filter((part) => {
    const { box } = part;
    const size = Math.max(box[1][0] - box[0][0], box[1][2] - box[0][2]);
    const cx = (box[0][0] + box[1][0]) / 2, cz = (box[0][2] + box[1][2]) / 2;
    // Glow planes can be larger than set pieces but still belong to the arena.
    return (size <= ARENA_MAX_PART || part.glow) && Math.hypot(cx, cz) <= ARENA_MAX_CENTRE;
  });
  // An import copy is dropped when the board places the same mesh in the arena
  // itself; where the placed instance is far away, the copy stands in for it.
  const placedMeshes = new Set(near.filter((part) => !part.modelImport).map((part) => part.meshId));
  near = near.filter((part) => !part.modelImport || !placedMeshes.has(part.meshId));
  // The playing surface is the Gameboard plane(s); the arena is fitted to it.
  // Planes smaller than a deck dais are the default board's model left at its
  // import scale (1/100), not this board's surface.
  const span = ({ box }) => Math.max(box[1][0] - box[0][0], box[1][2] - box[0][2]);
  const surface = near.filter((p) => p.name.startsWith("Gameboard") && span(p) >= MIN_SURFACE_SPAN);
  const basis = surface.length ? surface : near;
  const extent = [[Infinity, Infinity, Infinity], [-Infinity, -Infinity, -Infinity]];
  for (const { box } of basis) for (let k = 0; k < 3; k++) {
    extent[0][k] = Math.min(extent[0][k], box[0][k]);
    extent[1][k] = Math.max(extent[1][k], box[1][k]);
  }
  return { name: bundle.names[0] || "", parts: near, surface: extent, emitters };
}

// A Unity MinMaxCurve as [low, high]: a constant, or a random pick between two
// constants (state 2). Curves are approximated by their scalar.


// Largest horizontal size of a battleboard part kept in the arena, and how far
// from the origin its centre may be (world units, Unity metres).
const ARENA_MAX_PART = 150;
const ARENA_MAX_CENTRE = 130;
const MIN_SURFACE_SPAN = 10;
