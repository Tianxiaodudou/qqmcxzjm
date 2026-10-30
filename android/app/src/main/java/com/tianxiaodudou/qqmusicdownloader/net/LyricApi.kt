package com.tianxiaodudou.qqmusicdownloader.net

import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.util.zip.Inflater
import javax.crypto.Cipher
import javax.crypto.spec.SecretKeySpec

/**
 * 歌词：优先取 QRC 逐字歌词（hex -> 3DES -> zlib -> XML），拿不到就退回 LRC 逐行。
 * 与 fpk 的 parseQrc / fetchLyrics 同一套规则。
 */
object LyricApi {

    data class Word(val text: String, val startMs: Int, val durMs: Int)

    data class Line(
        val timeMs: Int,
        val text: String,
        val words: List<Word>,
        val translation: String = "",
    )

    private const val QRC_KEY = "!@#)(*$%123ZXC!@!@#)(NHL"

    fun fetch(mid: String): List<Line> {
        val p = JSONObject()
        p.put("songMid", mid)
        p.put("songId", 0)
        p.put("lrc_t", 0)
        p.put("qrc", 1)
        p.put("qrc_t", 0)
        p.put("roma", 0)
        p.put("roma_t", 0)
        p.put("trans", 1)
        p.put("trans_t", 0)
        p.put("needSingingAnnotations", false)
        p.put("type", 0)
        val data = runCatching {
            QqClient.cgi("music.musichallSong.PlayLyricInfo", "GetPlayLyricInfo", p)
        }.getOrNull() ?: return emptyList()

        val lyric = data.optString("lyric").orEmpty()
        val lrc = data.optString("lrc").orEmpty()
        val trans = data.optString("trans").orEmpty()
        val qrcXml = qrcXmlOf(lyric)
        // QRC 用的是 QQ 自己的 3DES 变体（与标准 DESede 不同），还没移植完；
        // 拿不到逐字歌词时用接口同时下发的普通 LRC，至少保证有歌词、能跟着滚。
        val lines = if (qrcXml != null) parseQrc(qrcXml) else parseLrc(decodeLrc(lrc.ifEmpty { lyric }))
        if (lines.isEmpty()) return lines
        val tr = parseLrc(decodeLrc(trans))
        return attachTranslation(lines, tr)
    }

    /** 上游把 QRC 的 xml 塞在 lyric 里（加密），先解密再判断。 */
    private fun qrcXmlOf(raw: String): String? {
        if (raw.isEmpty()) return null
        if (raw.contains("<QrcInfos")) return raw
        val plain = decryptQrc(raw) ?: return null
        return if (plain.contains("<QrcInfos")) plain else null
    }

    fun decryptQrc(hex: String): String? = runCatching {
        val bytes = ByteArray(hex.length / 2) {
            hex.substring(it * 2, it * 2 + 2).toInt(16).toByte()
        }
        val cipher = Cipher.getInstance("DESede/ECB/PKCS5Padding")
        cipher.init(Cipher.DECRYPT_MODE, SecretKeySpec(QRC_KEY.toByteArray(Charsets.US_ASCII), "DESede"))
        val dec = cipher.doFinal(bytes)
        inflate(dec).toString(Charsets.UTF_8)
    }.getOrNull()

    private fun inflate(src: ByteArray): ByteArray {
        val inflater = Inflater()
        inflater.setInput(src)
        val out = ByteArrayOutputStream()
        val buf = ByteArray(8192)
        while (!inflater.finished()) {
            val n = inflater.inflate(buf)
            if (n == 0) {
                if (inflater.needsInput() || inflater.needsDictionary()) break
            } else out.write(buf, 0, n)
        }
        inflater.end()
        return out.toByteArray()
    }

    private fun decodeLrc(raw: String): String {
        if (raw.isEmpty()) return ""
        val text = runCatching { String(android.util.Base64.decode(raw, android.util.Base64.DEFAULT), Charsets.UTF_8) }.getOrNull()
        return if (text != null && text.contains("]")) text else raw
    }

    private fun parseLrc(lrc: String): List<Line> {
        if (lrc.isEmpty()) return emptyList()
        val out = ArrayList<Line>()
        for (row in lrc.split("\n")) {
            val m = Regex("""\[(\d+):(\d+)(?:[.:](\d+))?](.*)""").find(row) ?: continue
            val min = m.groupValues[1].toIntOrNull() ?: continue
            val sec = m.groupValues[2].toIntOrNull() ?: continue
            val frac = m.groupValues[3]
            val ms = if (frac.isEmpty()) 0 else frac.padEnd(3, '0').take(3).toInt()
            val text = m.groupValues[4].trim()
            if (text.isEmpty()) continue
            out.add(Line(min * 60000 + sec * 1000 + ms, text, emptyList()))
        }
        out.sortBy { it.timeMs }
        return out
    }

    private fun parseQrc(xml: String): List<Line> {
        val out = ArrayList<Line>()
        val lineRe = Regex("""\[(\d+),(\d+)]([^\[]*)""")
        val wordRe = Regex("""([^()]*)\((\d+),(\d+)\)""")
        for (m in lineRe.findAll(xml)) {
            val lineStart = m.groupValues[1].toIntOrNull() ?: continue
            val body = m.groupValues[3]
            val words = wordRe.findAll(body).mapNotNull { w ->
                val text = w.groupValues[1]
                val s = w.groupValues[2].toIntOrNull() ?: return@mapNotNull null
                val d = w.groupValues[3].toIntOrNull() ?: 0
                if (text.isEmpty()) null else Word(text, s, d)
            }.toList()
            if (words.isEmpty()) continue
            val relative = words.first().startMs < lineStart
            val fixed = words.map { if (relative) it.copy(startMs = it.startMs + lineStart) else it.copy(startMs = it.startMs) }
            out.add(Line(lineStart, fixed.joinToString("") { it.text }, fixed))
        }
        out.sortBy { it.timeMs }
        return out
    }

    private fun attachTranslation(lines: List<Line>, trans: List<Line>): List<Line> {
        if (trans.isEmpty()) return lines
        return lines.map { l ->
            val hit = trans.minByOrNull { kotlin.math.abs(it.timeMs - l.timeMs) }
            if (hit != null && kotlin.math.abs(hit.timeMs - l.timeMs) <= 600) l.copy(translation = hit.text) else l
        }
    }
}
