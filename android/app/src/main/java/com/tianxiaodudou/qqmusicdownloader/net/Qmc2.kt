package com.tianxiaodudou.qqmusicdownloader.net

import kotlin.math.abs
import kotlin.math.tan

/**
 * QMC2 解密（MapCipher / RC4Cipher + 腾讯 TEA 派生），
 * 与 app/web/decrypt.py、app/web/csrc/qmc2_fast.c 逐字节等价。
 */
object Qmc2 {

    fun decrypt(buf: ByteArray, key: ByteArray, offset: Int = 0) {
        if (key.isEmpty()) return
        if (key.size > 300) rc4Decrypt(key, buf, buf.size, offset) else mapDecrypt(key, buf, buf.size, offset)
    }

    // ---------------- MapCipher ----------------

    private fun rotate(value: Int, bits: Int): Int {
        val shift = (bits + 4) % 8
        val v = value and 0xFF
        return ((v shl shift) or (v ushr (8 - shift))) and 0xFF
    }

    private fun mask(key: ByteArray, offset: Int): Int {
        if (key.isEmpty()) return 0
        var off = offset
        if (off > 0x7FFF) off %= 0x7FFF
        val idx = (off.toLong() * off.toLong() + 71214) % key.size
        return rotate(key[idx.toInt()].toInt(), idx.toInt() and 0x07)
    }

    private fun mapDecrypt(key: ByteArray, buf: ByteArray, len: Int, offset: Int) {
        for (i in 0 until len) buf[i] = (buf[i].toInt() xor mask(key, offset + i)).toByte()
    }

    // ---------------- RC4Cipher ----------------

    private fun computeHash(key: ByteArray): Long {
        var h = 1L
        for (k in key) {
            if (k.toInt() == 0) continue
            val nh = h * (k.toInt() and 0xFF)
            if (nh == 0L || nh <= h) break
            h = nh
        }
        return h
    }

    private fun segmentSkip(key: ByteArray, hash: Long, id: Int): Int {
        val seed = key[id % key.size].toLong() and 0xFF
        if (seed == 0L) return 0
        val idx = (hash.toDouble() / ((id + 1) * seed).toDouble() * 100.0).toLong()
        return (idx % key.size).toInt()
    }

    private fun rc4Decrypt(key: ByteArray, buf: ByteArray, len: Int, offset: Int) {
        val n = key.size
        val box = IntArray(n) { it and 0xFF }
        var j = 0
        for (i in 0 until n) {
            j = (j + box[i] + (key[i].toInt() and 0xFF)) % n
            val t = box[i]; box[i] = box[j]; box[j] = t
        }
        val hash = computeHash(key)
        val firstSeg = 128
        val segSize = 5120
        var processed = 0
        var pos = offset

        if (pos < firstSeg) {
            val block = minOf(len, firstSeg - pos)
            for (i in 0 until block) {
                val skip = segmentSkip(key, hash, pos + i)
                buf[processed + i] = (buf[processed + i].toInt() xor (key[skip].toInt() and 0xFF)).toByte()
            }
            processed += block
            pos += block
        }
        while (processed < len) {
            val segOffset = pos % segSize
            val block = minOf(segSize - segOffset, len - processed)
            val copy = box.copyOf()
            var jj = 0
            var kk = 0
            val skipLen = segOffset + segmentSkip(key, hash, pos / segSize)
            var i = -skipLen
            while (i < block) {
                jj = (jj + 1) % n
                kk = (copy[jj] + kk) % n
                val t = copy[jj]; copy[jj] = copy[kk]; copy[kk] = t
                if (i >= 0) {
                    val idx = (copy[jj] + copy[kk]) % n
                    buf[processed + i] = (buf[processed + i].toInt() xor copy[idx]).toByte()
                }
                i++
            }
            processed += block
            pos += block
        }
    }

    // ---------------- TEA 密钥派生 ----------------

    private fun simpleMakeKey(salt: Int, length: Int): ByteArray =
        ByteArray(length) { i -> (abs(tan(salt.toDouble() + i * 0.1)) * 100.0).toInt().and(0xFF).toByte() }

    private fun teaDecryptBlock(block: ByteArray, key: ByteArray): ByteArray {
        fun u32(b: ByteArray, o: Int): Long =
            ((b[o].toLong() and 0xFF) shl 24) or ((b[o + 1].toLong() and 0xFF) shl 16) or
                ((b[o + 2].toLong() and 0xFF) shl 8) or (b[o + 3].toLong() and 0xFF)
        var v0 = u32(block, 0)
        var v1 = u32(block, 4)
        val k0 = u32(key, 0); val k1 = u32(key, 4); val k2 = u32(key, 8); val k3 = u32(key, 12)
        val delta = 0x9E3779B9L
        var total = (delta * 16) and 0xFFFFFFFFL
        repeat(16) {
            v1 = (v1 - ((((v0 shl 4) and 0xFFFFFFFFL) + k2) xor ((v0 + total) and 0xFFFFFFFFL) xor (((v0 ushr 5) + k3) and 0xFFFFFFFFL))) and 0xFFFFFFFFL
            v0 = (v0 - ((((v1 shl 4) and 0xFFFFFFFFL) + k0) xor ((v1 + total) and 0xFFFFFFFFL) xor (((v1 ushr 5) + k1) and 0xFFFFFFFFL))) and 0xFFFFFFFFL
            total = (total - delta) and 0xFFFFFFFFL
        }
        val out = ByteArray(8)
        for (i in 0 until 4) {
            out[i] = ((v0 ushr (24 - i * 8)) and 0xFF).toByte()
            out[4 + i] = ((v1 ushr (24 - i * 8)) and 0xFF).toByte()
        }
        return out
    }

    private fun teaDecrypt(inBuf: ByteArray, key: ByteArray): ByteArray? {
        if (inBuf.size % 8 != 0 || inBuf.size < 16) return null
        var dest = teaDecryptBlock(inBuf.copyOfRange(0, 8), key)
        val padLen = dest[0].toInt() and 0x07
        val outLen = inBuf.size - 1 - padLen - 2 - 7
        if (outLen <= 0) return null
        val out = ByteArray(outLen)
        var ivPrev = ByteArray(8)
        var ivCur = inBuf.copyOfRange(0, 8)
        var inPos = 8
        var destIdx = 1 + padLen

        fun cryptBlock() {
            ivPrev = ivCur
            ivCur = inBuf.copyOfRange(inPos, inPos + 8)
            val tmp = ByteArray(8) { i -> (dest[i].toInt() xor inBuf[inPos + i].toInt()).toByte() }
            dest = teaDecryptBlock(tmp, key)
            inPos += 8
            destIdx = 0
        }

        var i = 0
        while (i < 2) {
            if (destIdx < 8) { destIdx++; i++ } else cryptBlock()
        }
        var outPos = 0
        while (outPos < outLen) {
            if (destIdx < 8) {
                out[outPos] = (dest[destIdx].toInt() xor ivPrev[destIdx].toInt()).toByte()
                destIdx++; outPos++
            } else cryptBlock()
        }
        return out
    }

    /** ekey(base64) -> 最终密钥；失败返回 null。 */
    fun deriveKey(ekeyB64: String): ByteArray? {
        val raw = runCatching { android.util.Base64.decode(ekeyB64.trim(), android.util.Base64.DEFAULT) }.getOrNull() ?: return null
        if (raw.size < 16) return null
        val simple = simpleMakeKey(106, 8)
        val teaKey = ByteArray(16)
        for (i in 0 until 8) {
            teaKey[i * 2] = simple[i]
            teaKey[i * 2 + 1] = raw[i]
        }
        val rs = teaDecrypt(raw.copyOfRange(8, raw.size), teaKey) ?: return null
        val out = ByteArray(8 + rs.size)
        System.arraycopy(raw, 0, out, 0, 8)
        System.arraycopy(rs, 0, out, 8, rs.size)
        return out
    }
}
