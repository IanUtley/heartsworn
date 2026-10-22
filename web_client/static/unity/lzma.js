// LZMA decoder for UnityFS blocks. A block is a raw LZMA stream: one property
// byte (lc, lp, pb), a 32-bit dictionary size, then the range-coded data. The
// output size comes from the bundle's block table, so no end marker is read.

const PROB_INIT = 1024;          // half of the 11-bit probability range
const MOVE_BITS = 5;
const TOP = 0x1000000;           // renormalise below 2^24
const END_POS_MODEL = 14;
const FULL_DISTANCES = 128;

class RangeDecoder {
  constructor(src, pos) {
    this.src = src;
    this.pos = pos + 1;          // the first byte of the stream is always zero
    this.range = 0xffffffff;
    this.code = 0;
    for (let i = 0; i < 4; i++) this.code = this.code * 256 + src[this.pos++];
  }

  normalise() {
    while (this.range < TOP) {
      this.range *= 256;
      this.code = this.code * 256 + this.src[this.pos++];
    }
  }

  bit(probs, index) {
    const p = probs[index];
    const bound = Math.floor(this.range / 2048) * p;
    let bit;
    if (this.code < bound) {
      this.range = bound;
      probs[index] = p + ((2048 - p) >> MOVE_BITS);
      bit = 0;
    } else {
      this.range -= bound;
      this.code -= bound;
      probs[index] = p - (p >> MOVE_BITS);
      bit = 1;
    }
    this.normalise();
    return bit;
  }

  direct(count) {
    let result = 0;
    for (let i = 0; i < count; i++) {
      this.range = Math.floor(this.range / 2);
      let bit = 0;
      if (this.code >= this.range) {
        this.code -= this.range;
        bit = 1;
      }
      result = result * 2 + bit;
      this.normalise();
    }
    return result;
  }

  // Bit tree, most significant bit first, probabilities at base + node.
  tree(probs, base, bits) {
    let node = 1;
    for (let i = 0; i < bits; i++) node = (node << 1) | this.bit(probs, base + node);
    return node - (1 << bits);
  }

  // Bit tree, least significant bit first.
  reverseTree(probs, base, bits) {
    let node = 1, symbol = 0;
    for (let i = 0; i < bits; i++) {
      const bit = this.bit(probs, base + node);
      node = (node << 1) | bit;
      symbol |= bit << i;
    }
    return symbol;
  }
}

class LengthDecoder {
  constructor() {
    this.choice = new Uint16Array(2).fill(PROB_INIT);
    this.low = new Uint16Array(16 * 8).fill(PROB_INIT);
    this.mid = new Uint16Array(16 * 8).fill(PROB_INIT);
    this.high = new Uint16Array(256).fill(PROB_INIT);
  }

  decode(rc, posState) {
    if (rc.bit(this.choice, 0) === 0) return rc.tree(this.low, posState << 3, 3);
    if (rc.bit(this.choice, 1) === 0) return 8 + rc.tree(this.mid, posState << 3, 3);
    return 16 + rc.tree(this.high, 0, 8);
  }
}

export function lzmaDecompress(src, outSize) {
  const props = src[0];
  const lc = props % 9;
  const lp = Math.floor(props / 9) % 5;
  const pb = Math.floor(props / 45);
  const out = new Uint8Array(outSize);
  const rc = new RangeDecoder(src, 5);     // range-coded data follows props and dictionary size

  const literal = new Uint16Array(0x300 << (lc + lp)).fill(PROB_INIT);
  const isMatch = new Uint16Array(12 << 4).fill(PROB_INIT);
  const isRep = new Uint16Array(12).fill(PROB_INIT);
  const isRepG0 = new Uint16Array(12).fill(PROB_INIT);
  const isRepG1 = new Uint16Array(12).fill(PROB_INIT);
  const isRepG2 = new Uint16Array(12).fill(PROB_INIT);
  const isRep0Long = new Uint16Array(12 << 4).fill(PROB_INIT);
  const posSlot = new Uint16Array(4 << 6).fill(PROB_INIT);
  const posDecoders = new Uint16Array(1 + FULL_DISTANCES - END_POS_MODEL).fill(PROB_INIT);
  const align = new Uint16Array(16).fill(PROB_INIT);
  const lenDecoder = new LengthDecoder();
  const repLenDecoder = new LengthDecoder();

  let state = 0, rep0 = 0, rep1 = 0, rep2 = 0, rep3 = 0;
  const posMask = (1 << pb) - 1;
  let pos = 0;
  while (pos < outSize) {
    const posState = pos & posMask;
    if (rc.bit(isMatch, (state << 4) + posState) === 0) {
      const prev = pos > 0 ? out[pos - 1] : 0;
      const base = 0x300 * (((pos & ((1 << lp) - 1)) << lc) + (prev >> (8 - lc)));
      let symbol = 1;
      if (state >= 7) {
        let matchByte = out[pos - rep0 - 1];
        do {
          const matchBit = (matchByte >> 7) & 1;
          matchByte <<= 1;
          const bit = rc.bit(literal, base + ((1 + matchBit) << 8) + symbol);
          symbol = (symbol << 1) | bit;
          if (matchBit !== bit) break;
        } while (symbol < 0x100);
      }
      while (symbol < 0x100) symbol = (symbol << 1) | rc.bit(literal, base + symbol);
      out[pos++] = symbol & 0xff;
      state = state < 4 ? 0 : state < 10 ? state - 3 : state - 6;
      continue;
    }

    let length;
    if (rc.bit(isRep, state) === 1) {
      if (rc.bit(isRepG0, state) === 0) {
        if (rc.bit(isRep0Long, (state << 4) + posState) === 0) {
          // A short rep: one byte from distance rep0.
          state = state < 7 ? 9 : 11;
          out[pos] = out[pos - rep0 - 1];
          pos++;
          continue;
        }
      } else {
        let distance;
        if (rc.bit(isRepG1, state) === 0) {
          distance = rep1;
        } else {
          if (rc.bit(isRepG2, state) === 0) {
            distance = rep2;
          } else {
            distance = rep3;
            rep3 = rep2;
          }
          rep2 = rep1;
        }
        rep1 = rep0;
        rep0 = distance;
      }
      length = repLenDecoder.decode(rc, posState);
      state = state < 7 ? 8 : 11;
    } else {
      rep3 = rep2;
      rep2 = rep1;
      rep1 = rep0;
      length = lenDecoder.decode(rc, posState);
      state = state < 7 ? 7 : 10;
      // Distance: a 6-bit slot chosen by the length, then direct and aligned bits.
      const lenState = Math.min(length, 3);
      const slot = rc.tree(posSlot, lenState << 6, 6);
      if (slot < 4) {
        rep0 = slot;
      } else {
        const bits = (slot >> 1) - 1;
        let distance = (2 | (slot & 1)) * 2 ** bits;
        if (slot < END_POS_MODEL) {
          distance += rc.reverseTree(posDecoders, distance - slot, bits);
        } else {
          distance += rc.direct(bits - 4) * 16;
          distance += rc.reverseTree(align, 0, 4);
        }
        if (distance === 0xffffffff) break;     // end marker
        rep0 = distance;
      }
    }

    length += 2;
    for (let i = 0; i < length && pos < outSize; i++) {
      out[pos] = out[pos - rep0 - 1];
      pos++;
    }
  }
  return out;
}
