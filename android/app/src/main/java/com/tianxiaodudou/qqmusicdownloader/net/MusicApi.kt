package com.tianxiaodudou.qqmusicdownloader.net

import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

object MusicApi {

    data class Song(
        val id: Int,
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
            parseSong(arr.optJSONObject(i))?.let { out.add(it) }
        }
        return out
    }

    fun parseSong(o: JSONObject?): Song? {
        if (o == null) return null
        val mid = o.optString("mid")
        if (mid.isEmpty()) return null
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
        return Song(
            id = o.optInt("id", o.optInt("songId", 0)),
            mid = mid,
            name = o.optString("name", o.optString("title")),
            singers = names.toString(),
            album = album?.optString("name").orEmpty(),
            albumMid = album?.optString("mid").orEmpty(),
            interval = o.optInt("interval", 0),
            mediaMid = o.optJSONObject("file")?.optString("media_mid").orEmpty().ifEmpty { mid },
        )
    }

    /** 依次尝试 320k / 128k，返回可播放的直链。 */
    /** 按数字 songid 取详情（首页信息流的歌曲卡只带数字 ID）。 */
    fun detail(songId: Long): Song? {
        val p = JSONObject()
        p.put("songid", songId)
        val data = QqClient.cgi("music.trackInfo.UnifiedGetTrackInfo", "GetTrackInfo", p)
        val o = data.optJSONObject("track_info")
            ?: data.optJSONObject("trackInfo")
            ?: data.optJSONObject("info")
            ?: return null
        return parseSong(o)
    }

    fun songUrl(song: Song): String? {
        for (quality in listOf("M800", "M500")) {
            val url = vkey(song, quality)
            if (!url.isNullOrEmpty()) return url
        }
        return null
    }

    /** 无损：拿加密直链 + ekey。返回 null 表示这首歌没有 SQ 权限。 */
    fun lossless(song: Song): Pair<String, String>? {
        val p = JSONObject()
        p.put("uin", QqClient.session?.uin ?: "0")
        p.put("filename", JSONArray().put("F000" + song.mediaMid + ".mflac"))
        p.put("guid", QqClient.guid)
        p.put("songmid", JSONArray().put(song.mid))
        p.put("songtype", JSONArray().put(0))
        p.put("ctx", 0)
        val data = runCatching { QqClient.cgi("music.vkey.GetEVkey", "CgiGetEVkey", p) }.getOrNull() ?: return null
        val info = data.optJSONArray("midurlinfo")?.optJSONObject(0) ?: return null
        val purl = info.optString("purl").orEmpty()
        val ekey = info.optString("ekey").orEmpty()
        if (purl.isEmpty() || ekey.isEmpty()) return null
        return Pair(if (purl.startsWith("http")) purl else "https://dl.stream.qqmusic.qq.com/" + purl, ekey)
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
