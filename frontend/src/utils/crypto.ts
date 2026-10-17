/**
 * Cryptographic helpers for Vayom Drive adhering strictly to Section 0 & Section 2
 * of update.md:
 * - Zero-Overhead AES-256-CTR with 64-bit Big-Endian counter block math.
 * - Authenticated AES-256-GCM for tamper-proof index catalog and metadata.
 * - PBKDF2-HMAC-SHA256 client-side key derivation.
 */

/**
 * Builds the 16-byte counter block for AES-256-CTR at any 16-byte aligned offset.
 * Exact implementation from Invariant 1 of update.md Section 0.
 */
export function buildCtrCounter(nonceHex: string, alignedByteOffset: number): Uint8Array {
  if (alignedByteOffset % 16 !== 0) {
    throw new Error(`AES-CTR offset (${alignedByteOffset}) must be a multiple of 16 bytes.`)
  }
  const cleanHex = nonceHex.trim().toLowerCase()
  if (cleanHex.length !== 16) {
    throw new Error(`AES-CTR nonce must be exactly 16 hex characters (8 bytes), got ${nonceHex}`)
  }
  const counterBuffer = new Uint8Array(16)
  // Bytes 0..7: 8-byte unique file nonce
  for (let i = 0; i < 8; i++) {
    counterBuffer[i] = parseInt(cleanHex.slice(i * 2, i * 2 + 2), 16)
  }
  // Bytes 8..15: 64-bit Big-Endian AES block index (byteOffset / 16)
  const blockIndex = BigInt(Math.floor(alignedByteOffset / 16))
  const view = new DataView(counterBuffer.buffer)
  view.setBigUint64(8, blockIndex, false) // false = big-endian
  return counterBuffer
}

/**
 * Generates a fresh random 8-byte hex nonce (16 hex chars) per file.
 */
export function generateFileNonce(): string {
  const bytes = new Uint8Array(8)
  window.crypto.getRandomValues(bytes)
  return Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

/**
 * Derives a 256-bit AES CryptoKey from a user passphrase using PBKDF2-HMAC-SHA256 (100,000 iterations).
 */
export async function deriveKeyFromPassword(
  passphrase: string,
  salt: Uint8Array = new TextEncoder().encode('vayom_drive_salt_v1')
): Promise<CryptoKey> {
  const enc = new TextEncoder()
  const keyMaterial = await window.crypto.subtle.importKey(
    'raw',
    enc.encode(passphrase),
    { name: 'PBKDF2' },
    false,
    ['deriveKey']
  )

  return window.crypto.subtle.deriveKey(
    {
      name: 'PBKDF2',
      salt: salt as unknown as BufferSource,
      iterations: 100_000,
      hash: 'SHA-256',
    },
    keyMaterial,
    { name: 'AES-CTR', length: 256 },
    false,
    ['encrypt', 'decrypt']
  )
}

/**
 * Encrypts a chunk with AES-256-CTR starting at alignedByteOffset.
 * 1 byte plaintext === 1 byte ciphertext (Zero byte overhead).
 */
export async function encryptCtrChunk(
  chunk: ArrayBuffer,
  key: CryptoKey,
  nonceHex: string,
  alignedByteOffset: number
): Promise<ArrayBuffer> {
  const counter = buildCtrCounter(nonceHex, alignedByteOffset)
  return window.crypto.subtle.encrypt(
    {
      name: 'AES-CTR',
      counter: counter as unknown as BufferSource,
      length: 64, // 64-bit counter size (Bytes 8..15)
    },
    key,
    chunk
  )
}

/**
 * Decrypts a chunk with AES-256-CTR starting at alignedByteOffset.
 */
export async function decryptCtrChunk(
  ciphertext: ArrayBuffer,
  key: CryptoKey,
  nonceHex: string,
  alignedByteOffset: number
): Promise<ArrayBuffer> {
  const counter = buildCtrCounter(nonceHex, alignedByteOffset)
  return window.crypto.subtle.decrypt(
    {
      name: 'AES-CTR',
      counter: counter as unknown as BufferSource,
      length: 64,
    },
    key,
    ciphertext
  )
}

/**
 * Encrypts arbitrary payload with AES-256-GCM for authenticated master catalog storage.
 */
export async function encryptGcm(
  data: ArrayBuffer,
  key: CryptoKey
): Promise<{ ciphertext: ArrayBuffer; iv: Uint8Array }> {
  const iv = new Uint8Array(12)
  window.crypto.getRandomValues(iv)
  const ciphertext = await window.crypto.subtle.encrypt(
    {
      name: 'AES-GCM',
      iv: iv as unknown as BufferSource,
    },
    key,
    data
  )
  return { ciphertext, iv }
}

/**
 * Decrypts and validates an authenticated AES-256-GCM payload.
 */
export async function decryptGcm(
  ciphertext: ArrayBuffer,
  key: CryptoKey,
  iv: Uint8Array
): Promise<ArrayBuffer> {
  return window.crypto.subtle.decrypt(
    {
      name: 'AES-GCM',
      iv: iv as unknown as BufferSource,
    },
    key,
    ciphertext
  )
}
