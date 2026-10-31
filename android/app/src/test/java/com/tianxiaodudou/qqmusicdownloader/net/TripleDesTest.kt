package com.tianxiaodudou.qqmusicdownloader.net

import org.junit.Assert.assertEquals
import org.junit.Test

/** 与 Python 参考实现逐块对拍的回归测试（向量由 tools/gen_tripledes_kt.py 生成）。 */
class TripleDesTest {
    @Test
    fun matchesPythonReference() {
        val key = "!@#)(*\$%123ZXC!@!@#)(NHL".toByteArray(Charsets.US_ASCII)
        val sched = TripleDes.tripledesKeySetup(key, TripleDes.ENCRYPT)
        val cases = listOf(
            "9445de2e88a576a5" to "691ab9b5a209e60d",
            "c580567765525e55" to "1a5566b059d72657",
            "ce723746d8f1a11e" to "367a3b7b297483dc",
            "fe8b77815f2094e1" to "a0a90dd1ba5dd910",
            "02aa7a463b76c5a2" to "b217f5cb5385b677",
            "378e9195ffc24157" to "c76a59cbc942ac39",
            "0e81a987fefc6ec8" to "25e999d02a2a96a4",
            "f586bc7179107cda" to "97faf6da5812c31e",
            "1da778850efe0f94" to "0fc8bde5a19ea246",
            "2e729ba04a9bac97" to "4a906882e7704d3f",
            "185f6f2da1bf4ecc" to "95c1369bf1230644",
            "3e1d12e89de44ae2" to "7d483c36f3e22023",
            "ceb9340d363a89e7" to "4257605d61f06b90",
            "5b6b93564143bfc9" to "1c04af1cd4dd78e0",
            "f78a341f03e35770" to "62f70de31e9a5604",
            "d154aa2337879f53" to "fdf7294011743277",
            "d3930bcb46d1d3a0" to "9ef7dd28c75b04c0",
            "946c915d94fae033" to "a844fe07ea482cd2",
            "4a82ff7f4459db59" to "05fd371ed9c25ee3",
            "d6a0ac2e287dbc21" to "ce5ba296ef4e01b1",
            "7533ad8c26a5d06e" to "7592b46a008fb312",
            "4defee3eb165fd03" to "5627929d33a1bea2",
            "d3f09775498bb804" to "f20ed8f6fa1e8bdb",
            "589ca4c5dbdceea8" to "e0a59144154c879a",
        )
        for ((p, c) in cases) {
            assertEquals("block " + p, c, hex(TripleDes.tripledesCrypt(unhex(p), sched)))
        }
    }

    private fun unhex(s: String) = ByteArray(s.length / 2) { s.substring(it * 2, it * 2 + 2).toInt(16).toByte() }
    private fun hex(b: ByteArray) = b.joinToString("") { String.format("%02x", it) }
}
