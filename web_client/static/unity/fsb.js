// FMOD sound banks (FSB5), which Unity keeps in AudioClip resources. The card
// clips hold one Vorbis sample each. FSB drops the Vorbis identification and
// setup headers and stores packets with a 16-bit size, so this rebuilds an Ogg
// Vorbis stream the browser's own decoder can play.

import { VORBIS_SETUPS } from "./vorbis_setups.js";

const FSB_HEADER = 60;            // size of the FSB5 header before the sample header
const FMOD_VORBIS = 15;
const LONG_BLOCK = 2048, SHORT_BLOCK = 256;   // FSB's fixed Vorbis block sizes
const LOG2 = { 256: 8, 2048: 11 };

// Reads the sample header and chunks of the first sample. Returns the Vorbis
// setup id and the Ogg-ready packet list, or null for other FSB codecs.
export function parseVorbisBank(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (new TextDecoder().decode(bytes.subarray(0, 4)) !== "FSB5") throw new Error("not an FSB5 bank");
  const sampleHeaderSize = view.getUint32(12, true);
  const nameTableSize = view.getUint32(16, true);
  const dataSize = view.getUint32(20, true);
  if (view.getUint32(24, true) !== FMOD_VORBIS) return null;

  const mode = view.getBigUint64(FSB_HEADER, true);
  const samples = Number((mode >> 34n) & 0x3fffffffn);
  const dataOffset = Number((mode >> 7n) & 0x07ffffffn) * 32;
  let channels = [1, 2, 6, 8][Number((mode >> 5n) & 3n)];
  let setupId = null;
  let at = FSB_HEADER + 8;
  if (mode & 1n) {
    for (;;) {
      const word = view.getUint32(at, true);
      const type = (word >>> 25) & 0x7f;
      const size = (word >>> 1) & 0xffffff;
      if (type === 0x01) channels = view.getUint8(at + 4);
      if (type === 0x0b) setupId = view.getUint32(at + 4, true).toString(16).padStart(8, "0");
      at += 4 + size;
      if (!(word & 1)) break;
    }
  }
  if (!setupId || !VORBIS_SETUPS[setupId]) throw new Error(`no Vorbis setup for bank ${setupId}`);

  // Packets: a 16-bit size, then the packet, until the stream's end.
  const start = FSB_HEADER + sampleHeaderSize + nameTableSize + dataOffset;
  const end = FSB_HEADER + sampleHeaderSize + nameTableSize + dataSize;
  const packets = [];
  for (let p = start; p + 2 <= end;) {
    const size = view.getUint16(p, true);
    if (size === 0 || size === 0xffff) break;
    packets.push(bytes.subarray(p + 2, p + 2 + size));
    p += 2 + size;
  }
  return { channels, samples, setupId, packets };
}

// Ogg Vorbis from an FSB bank: identification, comment and setup headers, then
// one page per audio packet. Returns the bytes of an .ogg file.
export function vorbisBankToOgg(bank, sampleRate) {
  const id = new Uint8Array(30);
  const idView = new DataView(id.buffer);
  id[0] = 0x01;
  id.set(new TextEncoder().encode("vorbis"), 1);
  idView.setUint32(7, 0, true);
  id[11] = bank.channels;
  idView.setUint32(12, sampleRate, true);
  id[28] = (LOG2[LONG_BLOCK] << 4) | LOG2[SHORT_BLOCK];
  id[29] = 1;

  const comment = new Uint8Array(0x19);
  comment[0] = 0x03;
  comment.set(new TextEncoder().encode("vorbis"), 1);
  new DataView(comment.buffer).setUint32(7, 9, true);
  comment.set(new TextEncoder().encode("vgmstream"), 11);
  comment[0x18] = 1;

  const setup = base64ToBytes(VORBIS_SETUPS[bank.setupId]);
  const pages = [
    oggPage([id], 0x02, 0n, 0),
    oggPage([comment, setup], 0x00, 0n, 1),
  ];
  bank.packets.forEach((packet, i) => {
    const last = i === bank.packets.length - 1;
    // Only the last page carries a granule (the sample count); the others are -1.
    pages.push(oggPage([packet], last ? 0x04 : 0x00, last ? BigInt(bank.samples) : -1n, i + 2));
  });
  return concat(pages);
}

function base64ToBytes(text) {
  const binary = atob(text);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

// One Ogg page holding whole packets. Packets longer than 255 segments (65025
// bytes) are not used by these banks and are rejected.
function oggPage(packets, flags, granule, sequence) {
  const lacing = [];
  for (const packet of packets) {
    let left = packet.length;
    while (left >= 255) { lacing.push(255); left -= 255; }
    lacing.push(left);
  }
  if (lacing.length > 255) throw new Error("Ogg page has too many segments");
  const header = new Uint8Array(27 + lacing.length);
  const view = new DataView(header.buffer);
  header.set([0x4f, 0x67, 0x67, 0x53], 0);      // "OggS"
  header[5] = flags;
  view.setBigInt64(6, BigInt.asIntN(64, granule), true);
  view.setUint32(14, 1, true);                  // one logical stream
  view.setUint32(18, sequence, true);
  header[26] = lacing.length;
  header.set(lacing, 27);
  const body = concat(packets);
  const page = concat([header, body]);
  new DataView(page.buffer).setUint32(22, oggCrc(page), true);
  return page;
}

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let i = 0; i < 256; i++) {
    let r = i << 24;
    for (let k = 0; k < 8; k++) r = r & 0x80000000 ? (r << 1) ^ 0x04c11db7 : r << 1;
    table[i] = r >>> 0;
  }
  return table;
})();

// Ogg's CRC-32 (polynomial 0x04c11db7, no reflection), over a page with its CRC field zeroed.
function oggCrc(page) {
  let crc = 0;
  for (let i = 0; i < page.length; i++) {
    const byte = i >= 22 && i < 26 ? 0 : page[i];
    crc = ((crc << 8) ^ CRC_TABLE[((crc >>> 24) ^ byte) & 0xff]) >>> 0;
  }
  return crc;
}

function concat(parts) {
  const total = parts.reduce((n, p) => n + p.length, 0);
  const out = new Uint8Array(total);
  let at = 0;
  for (const part of parts) { out.set(part, at); at += part.length; }
  return out;
}
