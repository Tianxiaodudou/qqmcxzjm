package com.tianxiaodudou.qqmusicdownloader.net

import java.io.ByteArrayOutputStream
import java.util.zip.Inflater

/** QQ 音乐 QRC 使用的 3DES 变体（含 PC-2 偏移量偏差），由 app/qqmusic_api/algorithms/tripledes.py 机械移植，勿手改。 */
object TripleDes {
    const val ENCRYPT = 1
    const val DECRYPT = 0

    private val KEY_QRC = "!@#)(*$%123ZXC!@!@#)(NHL".toByteArray(Charsets.US_ASCII)

    private val SBOX = arrayOf(intArrayOf(14, 4, 13, 1, 2, 15, 11, 8, 3, 10, 6, 12, 5, 9, 0, 7, 0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11, 9, 5, 3, 8, 4, 1, 14, 8, 13, 6, 2, 11, 15, 12, 9, 7, 3, 10, 5, 0, 15, 12, 8, 2, 4, 9, 1, 7, 5, 11, 3, 14, 10, 0, 6, 13), intArrayOf(15, 1, 8, 14, 6, 11, 3, 4, 9, 7, 2, 13, 12, 0, 5, 10, 3, 13, 4, 7, 15, 2, 8, 15, 12, 0, 1, 10, 6, 9, 11, 5, 0, 14, 7, 11, 10, 4, 13, 1, 5, 8, 12, 6, 9, 3, 2, 15, 13, 8, 10, 1, 3, 15, 4, 2, 11, 6, 7, 12, 0, 5, 14, 9), intArrayOf(10, 0, 9, 14, 6, 3, 15, 5, 1, 13, 12, 7, 11, 4, 2, 8, 13, 7, 0, 9, 3, 4, 6, 10, 2, 8, 5, 14, 12, 11, 15, 1, 13, 6, 4, 9, 8, 15, 3, 0, 11, 1, 2, 12, 5, 10, 14, 7, 1, 10, 13, 0, 6, 9, 8, 7, 4, 15, 14, 3, 11, 5, 2, 12), intArrayOf(7, 13, 14, 3, 0, 6, 9, 10, 1, 2, 8, 5, 11, 12, 4, 15, 13, 8, 11, 5, 6, 15, 0, 3, 4, 7, 2, 12, 1, 10, 14, 9, 10, 6, 9, 0, 12, 11, 7, 13, 15, 1, 3, 14, 5, 2, 8, 4, 3, 15, 0, 6, 10, 10, 13, 8, 9, 4, 5, 11, 12, 7, 2, 14), intArrayOf(2, 12, 4, 1, 7, 10, 11, 6, 8, 5, 3, 15, 13, 0, 14, 9, 14, 11, 2, 12, 4, 7, 13, 1, 5, 0, 15, 10, 3, 9, 8, 6, 4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14, 11, 8, 12, 7, 1, 14, 2, 13, 6, 15, 0, 9, 10, 4, 5, 3), intArrayOf(12, 1, 10, 15, 9, 2, 6, 8, 0, 13, 3, 4, 14, 7, 5, 11, 10, 15, 4, 2, 7, 12, 9, 5, 6, 1, 13, 14, 0, 11, 3, 8, 9, 14, 15, 5, 2, 8, 12, 3, 7, 0, 4, 10, 1, 13, 11, 6, 4, 3, 2, 12, 9, 5, 15, 10, 11, 14, 1, 7, 6, 0, 8, 13), intArrayOf(4, 11, 2, 14, 15, 0, 8, 13, 3, 12, 9, 7, 5, 10, 6, 1, 13, 0, 11, 7, 4, 9, 1, 10, 14, 3, 5, 12, 2, 15, 8, 6, 1, 4, 11, 13, 12, 3, 7, 14, 10, 15, 6, 8, 0, 5, 9, 2, 6, 11, 13, 8, 1, 4, 10, 7, 9, 5, 0, 15, 14, 2, 3, 12), intArrayOf(13, 2, 8, 4, 6, 15, 11, 1, 10, 9, 3, 14, 5, 0, 12, 7, 1, 15, 13, 8, 10, 3, 7, 4, 12, 5, 6, 11, 0, 14, 9, 2, 7, 11, 4, 1, 9, 12, 14, 2, 0, 6, 10, 13, 15, 3, 5, 8, 2, 1, 14, 7, 4, 10, 8, 13, 15, 12, 9, 0, 3, 5, 6, 11))
    private val KEY_RND_SHIFT = intArrayOf(1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1)
    private val KEY_PERM_C = intArrayOf(56, 48, 40, 32, 24, 16, 8, 0, 57, 49, 41, 33, 25, 17, 9, 1, 58, 50, 42, 34, 26, 18, 10, 2, 59, 51, 43, 35)
    private val KEY_PERM_D = intArrayOf(62, 54, 46, 38, 30, 22, 14, 6, 61, 53, 45, 37, 29, 21, 13, 5, 60, 52, 44, 36, 28, 20, 12, 4, 27, 19, 11, 3)
    private val KEY_COMPRESSION = intArrayOf(13, 16, 10, 23, 0, 4, 2, 27, 14, 5, 20, 9, 22, 18, 11, 3, 25, 7, 15, 6, 26, 19, 12, 1, 40, 51, 30, 36, 46, 54, 29, 39, 50, 44, 32, 47, 43, 48, 38, 55, 33, 52, 45, 41, 49, 35, 28, 31)

    private fun sboxBit(a: Int): Int = (a and 32) or ((a and 31) shr 1) or ((a and 1) shl 4)

    fun initialPermutation(d: ByteArray): Pair<Int, Int> {
        val v0 = (d[0].toInt() and 0xFF) or ((d[1].toInt() and 0xFF) shl 8) or ((d[2].toInt() and 0xFF) shl 16) or ((d[3].toInt() and 0xFF) shl 24)
        val v1 = (d[4].toInt() and 0xFF) or ((d[5].toInt() and 0xFF) shl 8) or ((d[6].toInt() and 0xFF) shl 16) or ((d[7].toInt() and 0xFF) shl 24)
        val s0 = ((((((((((((((((((((((((((((((((((v1 shr 6) and 1) shl 31) or (((v1 shr 14) and 1) shl 30)) or (((v1 shr 22) and 1) shl 29)) or (((v1 shr 30) and 1) shl 28)) or (((v0 shr 6) and 1) shl 27)) or (((v0 shr 14) and 1) shl 26)) or (((v0 shr 22) and 1) shl 25)) or (((v0 shr 30) and 1) shl 24)) or (((v1 shr 4) and 1) shl 23)) or (((v1 shr 12) and 1) shl 22)) or (((v1 shr 20) and 1) shl 21)) or (((v1 shr 28) and 1) shl 20)) or (((v0 shr 4) and 1) shl 19)) or (((v0 shr 12) and 1) shl 18)) or (((v0 shr 20) and 1) shl 17)) or (((v0 shr 28) and 1) shl 16)) or (((v1 shr 2) and 1) shl 15)) or (((v1 shr 10) and 1) shl 14)) or (((v1 shr 18) and 1) shl 13)) or (((v1 shr 26) and 1) shl 12)) or (((v0 shr 2) and 1) shl 11)) or (((v0 shr 10) and 1) shl 10)) or (((v0 shr 18) and 1) shl 9)) or (((v0 shr 26) and 1) shl 8)) or (((v1 shr 0) and 1) shl 7)) or (((v1 shr 8) and 1) shl 6)) or (((v1 shr 16) and 1) shl 5)) or (((v1 shr 24) and 1) shl 4)) or (((v0 shr 0) and 1) shl 3)) or (((v0 shr 8) and 1) shl 2)) or (((v0 shr 16) and 1) shl 1)) or ((v0 shr 24) and 1))
        val s1 = ((((((((((((((((((((((((((((((((((v1 shr 7) and 1) shl 31) or (((v1 shr 15) and 1) shl 30)) or (((v1 shr 23) and 1) shl 29)) or (((v1 shr 31) and 1) shl 28)) or (((v0 shr 7) and 1) shl 27)) or (((v0 shr 15) and 1) shl 26)) or (((v0 shr 23) and 1) shl 25)) or (((v0 shr 31) and 1) shl 24)) or (((v1 shr 5) and 1) shl 23)) or (((v1 shr 13) and 1) shl 22)) or (((v1 shr 21) and 1) shl 21)) or (((v1 shr 29) and 1) shl 20)) or (((v0 shr 5) and 1) shl 19)) or (((v0 shr 13) and 1) shl 18)) or (((v0 shr 21) and 1) shl 17)) or (((v0 shr 29) and 1) shl 16)) or (((v1 shr 3) and 1) shl 15)) or (((v1 shr 11) and 1) shl 14)) or (((v1 shr 19) and 1) shl 13)) or (((v1 shr 27) and 1) shl 12)) or (((v0 shr 3) and 1) shl 11)) or (((v0 shr 11) and 1) shl 10)) or (((v0 shr 19) and 1) shl 9)) or (((v0 shr 27) and 1) shl 8)) or (((v1 shr 1) and 1) shl 7)) or (((v1 shr 9) and 1) shl 6)) or (((v1 shr 17) and 1) shl 5)) or (((v1 shr 25) and 1) shl 4)) or (((v0 shr 1) and 1) shl 3)) or (((v0 shr 9) and 1) shl 2)) or (((v0 shr 17) and 1) shl 1)) or ((v0 shr 25) and 1))
        return s0 to s1
    }

    fun inversePermutation(s0: Int, s1: Int): ByteArray {
        val data = ByteArray(8)
        data[0] = (((((((((((s1 shr 27) and 1) shl 7) or (((s0 shr 27) and 1) shl 6)) or (((s1 shr 19) and 1) shl 5)) or (((s0 shr 19) and 1) shl 4)) or (((s1 shr 11) and 1) shl 3)) or (((s0 shr 11) and 1) shl 2)) or (((s1 shr 3) and 1) shl 1)) or ((s0 shr 3) and 1))).toByte()
        data[1] = (((((((((((s1 shr 26) and 1) shl 7) or (((s0 shr 26) and 1) shl 6)) or (((s1 shr 18) and 1) shl 5)) or (((s0 shr 18) and 1) shl 4)) or (((s1 shr 10) and 1) shl 3)) or (((s0 shr 10) and 1) shl 2)) or (((s1 shr 2) and 1) shl 1)) or ((s0 shr 2) and 1))).toByte()
        data[2] = (((((((((((s1 shr 25) and 1) shl 7) or (((s0 shr 25) and 1) shl 6)) or (((s1 shr 17) and 1) shl 5)) or (((s0 shr 17) and 1) shl 4)) or (((s1 shr 9) and 1) shl 3)) or (((s0 shr 9) and 1) shl 2)) or (((s1 shr 1) and 1) shl 1)) or ((s0 shr 1) and 1))).toByte()
        data[3] = (((((((((((s1 shr 24) and 1) shl 7) or (((s0 shr 24) and 1) shl 6)) or (((s1 shr 16) and 1) shl 5)) or (((s0 shr 16) and 1) shl 4)) or (((s1 shr 8) and 1) shl 3)) or (((s0 shr 8) and 1) shl 2)) or (((s1 shr 0) and 1) shl 1)) or ((s0 shr 0) and 1))).toByte()
        data[4] = (((((((((((s1 shr 31) and 1) shl 7) or (((s0 shr 31) and 1) shl 6)) or (((s1 shr 23) and 1) shl 5)) or (((s0 shr 23) and 1) shl 4)) or (((s1 shr 15) and 1) shl 3)) or (((s0 shr 15) and 1) shl 2)) or (((s1 shr 7) and 1) shl 1)) or ((s0 shr 7) and 1))).toByte()
        data[5] = (((((((((((s1 shr 30) and 1) shl 7) or (((s0 shr 30) and 1) shl 6)) or (((s1 shr 22) and 1) shl 5)) or (((s0 shr 22) and 1) shl 4)) or (((s1 shr 14) and 1) shl 3)) or (((s0 shr 14) and 1) shl 2)) or (((s1 shr 6) and 1) shl 1)) or ((s0 shr 6) and 1))).toByte()
        data[6] = (((((((((((s1 shr 29) and 1) shl 7) or (((s0 shr 29) and 1) shl 6)) or (((s1 shr 21) and 1) shl 5)) or (((s0 shr 21) and 1) shl 4)) or (((s1 shr 13) and 1) shl 3)) or (((s0 shr 13) and 1) shl 2)) or (((s1 shr 5) and 1) shl 1)) or ((s0 shr 5) and 1))).toByte()
        data[7] = (((((((((((s1 shr 28) and 1) shl 7) or (((s0 shr 28) and 1) shl 6)) or (((s1 shr 20) and 1) shl 5)) or (((s0 shr 20) and 1) shl 4)) or (((s1 shr 12) and 1) shl 3)) or (((s0 shr 12) and 1) shl 2)) or (((s1 shr 4) and 1) shl 1)) or ((s0 shr 4) and 1))).toByte()
        return data
    }

    fun f(state0: Int, key: IntArray): Int {
        val t1 = ((((((state0 and 1) shl 31) or ((state0 and (0xf8000000).toInt()) shr 1)) or ((state0 and 528482304) shr 3)) or ((state0 and 33030144) shr 5)) or ((state0 and 2064384) shr 7))
        val t2 = ((((((state0 and 129024) shl 15) or ((state0 and 8064) shl 13)) or ((state0 and 504) shl 11)) or ((state0 and 31) shl 9)) or ((state0 and (0x80000000).toInt()) shr 23))
        val k0 = ((t1 shr 24) and 0xFF) xor key[0]
        val k1 = ((t1 shr 16) and 0xFF) xor key[1]
        val k2 = ((t1 shr 8) and 0xFF) xor key[2]
        val k3 = ((t2 shr 24) and 0xFF) xor key[3]
        val k4 = ((t2 shr 16) and 0xFF) xor key[4]
        val k5 = ((t2 shr 8) and 0xFF) xor key[5]
        val st = ((((((((SBOX[0][sboxBit((k0 shr 2))] shl 28) or (SBOX[1][sboxBit((((k0 and 3) shl 4) or (k1 shr 4)))] shl 24)) or (SBOX[2][sboxBit((((k1 and 15) shl 2) or (k2 shr 6)))] shl 20)) or (SBOX[3][sboxBit((k2 and 63))] shl 16)) or (SBOX[4][sboxBit((k3 shr 2))] shl 12)) or (SBOX[5][sboxBit((((k3 and 3) shl 4) or (k4 shr 4)))] shl 8)) or (SBOX[6][sboxBit((((k4 and 15) shl 2) or (k5 shr 6)))] shl 4)) or SBOX[7][sboxBit((k5 and 63))])
        return ((((((((((((((((((((((((((((((((((st shr 16) and 1) shl 31) or (((st shr 25) and 1) shl 30)) or (((st shr 12) and 1) shl 29)) or (((st shr 11) and 1) shl 28)) or (((st shr 3) and 1) shl 27)) or (((st shr 20) and 1) shl 26)) or (((st shr 4) and 1) shl 25)) or (((st shr 15) and 1) shl 24)) or (((st shr 31) and 1) shl 23)) or (((st shr 17) and 1) shl 22)) or (((st shr 9) and 1) shl 21)) or (((st shr 6) and 1) shl 20)) or (((st shr 27) and 1) shl 19)) or (((st shr 14) and 1) shl 18)) or (((st shr 1) and 1) shl 17)) or (((st shr 22) and 1) shl 16)) or (((st shr 30) and 1) shl 15)) or (((st shr 24) and 1) shl 14)) or (((st shr 8) and 1) shl 13)) or (((st shr 18) and 1) shl 12)) or (((st shr 0) and 1) shl 11)) or (((st shr 5) and 1) shl 10)) or (((st shr 29) and 1) shl 9)) or (((st shr 23) and 1) shl 8)) or (((st shr 13) and 1) shl 7)) or (((st shr 19) and 1) shl 6)) or (((st shr 2) and 1) shl 5)) or (((st shr 26) and 1) shl 4)) or (((st shr 10) and 1) shl 3)) or (((st shr 21) and 1) shl 2)) or (((st shr 28) and 1) shl 1)) or ((st shr 7) and 1))
    }

    fun crypt(input: ByteArray, key: Array<IntArray>): ByteArray {
        var s0: Int
        var s1: Int
        val ip = initialPermutation(input)
        s0 = ip.first
        s1 = ip.second
        for (idx in 0 until 15) {
            val prev = s1
            s1 = f(s1, key[idx]) xor s0
            s0 = prev
        }
        s0 = f(s1, key[15]) xor s0
        return inversePermutation(s0, s1)
    }

    fun keySchedule(key: ByteArray, mode: Int): Array<IntArray> {
        val schedule = Array(16) { IntArray(6) }
        val v0 = (key[0].toInt() and 0xFF) or ((key[1].toInt() and 0xFF) shl 8) or ((key[2].toInt() and 0xFF) shl 16) or ((key[3].toInt() and 0xFF) shl 24)
        val v1 = (key[4].toInt() and 0xFF) or ((key[5].toInt() and 0xFF) shl 8) or ((key[6].toInt() and 0xFF) shl 16) or ((key[7].toInt() and 0xFF) shl 24)
        var c = 0
        for ((i, b) in KEY_PERM_C.withIndex()) {
            val bit = if (b < 32) ((v0 shr (31 - b)) and 1) else ((v1 shr (63 - b)) and 1)
            c = c or (bit shl (31 - i))
        }
        var d = 0
        for ((i, b) in KEY_PERM_D.withIndex()) {
            val bit = if (b < 32) ((v0 shr (31 - b)) and 1) else ((v1 shr (63 - b)) and 1)
            d = d or (bit shl (31 - i))
        }
        for (i in 0 until 16) {
            val sh = KEY_RND_SHIFT[i]
            c = ((c shl sh) or (c ushr (28 - sh))) and (0xFFFFFFF0).toInt()
            d = ((d shl sh) or (d ushr (28 - sh))) and (0xFFFFFFF0).toInt()
            val togen = if (mode == DECRYPT) 15 - i else i
            for (j in 0 until 6) schedule[togen][j] = 0
            for (j in 0 until 24) {
                val bit = (c shr (31 - KEY_COMPRESSION[j])) and 1
                schedule[togen][j / 8] = schedule[togen][j / 8] or (bit shl (7 - (j % 8)))
            }
            for (j in 24 until 48) {
                val bit = (d shr (31 - (KEY_COMPRESSION[j] - 27))) and 1
                schedule[togen][j / 8] = schedule[togen][j / 8] or (bit shl (7 - (j % 8)))
            }
        }
        return schedule
    }

    fun tripledesKeySetup(key: ByteArray, mode: Int): Array<Array<IntArray>> = if (mode == ENCRYPT) {
        arrayOf(
            keySchedule(key.copyOfRange(0, 8), ENCRYPT),
            keySchedule(key.copyOfRange(8, 16), DECRYPT),
            keySchedule(key.copyOfRange(16, 24), ENCRYPT),
        )
    } else {
        arrayOf(
            keySchedule(key.copyOfRange(16, 24), DECRYPT),
            keySchedule(key.copyOfRange(8, 16), ENCRYPT),
            keySchedule(key.copyOfRange(0, 8), DECRYPT),
        )
    }

    fun tripledesCrypt(data: ByteArray, key: Array<Array<IntArray>>): ByteArray {
        var d = data.copyOf()
        for (i in 0 until 3) d = crypt(d, key[i])
        return d
    }

/** 解开 QRC 密文（hex -> 3DES -> zlib -> UTF-8），失败返回 null。 */
    fun qrcDecrypt(hex: String): String? {
        if (hex.isEmpty() || hex.length % 2 != 0) return null
        return try {
            val enc = ByteArray(hex.length / 2) { hex.substring(it * 2, it * 2 + 2).toInt(16).toByte() }
            val sched = tripledesKeySetup(KEY_QRC, DECRYPT)
            val out = ByteArray(enc.size)
            var i = 0
            while (i < enc.size) {
                val end = if (i + 8 <= enc.size) i + 8 else enc.size
                val dec = tripledesCrypt(enc.copyOfRange(i, end), sched)
                System.arraycopy(dec, 0, out, i, end - i)
                i += 8
            }
            val inflater = Inflater()
            inflater.setInput(out)
            val buf = ByteArray(1 shl 16)
            val bos = ByteArrayOutputStream()
            while (!inflater.finished()) {
                val n = inflater.inflate(buf)
                if (n == 0) break
                bos.write(buf, 0, n)
            }
            inflater.end()
            bos.toByteArray().toString(Charsets.UTF_8)
        } catch (e: Exception) {
            null
        }
    }
}
