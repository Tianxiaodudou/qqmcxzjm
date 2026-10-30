package com.tianxiaodudou.qqmusicdownloader.net

import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

object MusicApi {

    data class Song(
        val mid: String,
        val name: String,
        val singers: String,
        val album: String,
        val albumMid: String,
        val interval: Int,
        val mediaMid: String,
    ) {
        val subtitle: String get() = if (album.isEmpty()) singers else singers + " · " + album
    }

    fun search(keyword: String, page: Int = 1): List<Song> {
        val p = JSONObject()
        p.put("searchid", UUID.randomUUID().toString().replace("-", ""))
        p.put("query", keyword)
        p.put("search_type", 0)
        p.put("num_per_page", 30)
        p.put("page_num", page)
        p.put("highlight", true)
        p.put("grp", true)
        p.put("selectors", JSONObject())
        val data = QqClient.cgi("music.search.SearchCgiService", "DoSearchForQQMusicDesktop", p)
        val arr = data.optJSONObject("body")?.optJSONObject("song")?.optJSONArray("list") ?: return emptyList()
        val out = ArrayList<Song>(arr.length())
        for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            val mid = o.optString("mid")
            if (mid.isEmpty()) continue
            val names = StringBuilder()
            val singers = o.optJSONArray("singer")
            if (singers != null) {
                for (j in 0 until singers.length()) {
                    val s = singers.optJSONObject(j)?.optString("name").orEmpty()
                    if (s.isNotEmpty()) {
                        if (names.isNotEmpty()) names.append(" / ")
                        names.append(s)
                    }
                }
            }
            val album = o.optJSONObject("album")
            out.add(
                Song(
                    mid = mid,
                    name = o.optString("name", o.optString("title")),
                    singers = names.toString(),
                    album = album?.optString("name").orEmpty(),
                    albumMid = album?.optString("mid").orEmpty(),
                    interval = o.optInt("interval", 0),
                    mediaMid = o.optJSONObject("file")?.optString("media_mid").orEmpty().ifEmpty { mid },
                )
            )
        }
        return out
    }

    /** 依次尝试 320k / 128k，返回可播放的直链。 */
    fun songUrl(song: Song): String? {
        for (quality in listOf("M800", "M500")) {
            val url = vkey(song, quality)
            if (!url.isNullOrEmpty()) return url
        }
        return null
    }

    private fun vkey(song: Song, quality: String): String? {
        val p = JSONObject()
        p.put("uin", QqClient.session?.uin ?: "0")
        p.put("filename", JSONArray().put(quality + song.mediaMid + ".mp3"))
        p.put("guid", QqClient.guid)
        p.put("songmid", JSONArray().put(song.mid))
        p.put("songtype", JSONArray().put(0))
        p.put("ctx", 0)
        val data = runCatching { QqClient.cgi("music.vkey.GetVkey", "UrlGetVkey", p) }.getOrNull() ?: return null
        val info = data.optJSONArray("midurlinfo") ?: return null
        val purl = info.optJSONObject(0)?.optString("purl").orEmpty()
        if (purl.isEmpty()) return null
        return if (purl.startsWith("http")) purl else "https://dl.stream.qqmusic.qq.com/" + purl
    }
}
