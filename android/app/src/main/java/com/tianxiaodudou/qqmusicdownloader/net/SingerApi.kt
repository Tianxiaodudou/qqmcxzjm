package com.tianxiaodudou.qqmusicdownloader.net

import org.json.JSONObject

/** 热门歌手：歌手分类榜 + 歌手歌曲。 */
object SingerApi {

    data class Singer(val mid: String, val name: String)

    /** area/sex/genre 传 -100 表示全部。 */
    fun list(area: Int = -100, sex: Int = -100, genre: Int = -100): List<Singer> {
        val p = JSONObject()
        p.put("hastag", 0)
        p.put("area", area)
        p.put("sex", sex)
        p.put("genre", genre)
        val data = QqClient.cgi("music.musichallSinger.SingerList", "GetSingerList", p)
        val arr = data.optJSONArray("singerlist")
            ?: data.optJSONArray("singer_list")
            ?: return emptyList()
        val out = ArrayList<Singer>()
        for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            val mid = o.optString("mid").ifEmpty { o.optString("singer_mid") }
            if (mid.isEmpty()) continue
            val name = o.optString("name").ifEmpty { o.optString("singer_name") }
            if (name.isEmpty()) continue
            out.add(Singer(mid, name))
        }
        return out
    }

    fun songs(mid: String, num: Int = 50): List<MusicApi.Song> {
        val p = JSONObject()
        p.put("singerMid", mid)
        p.put("order", 1)
        p.put("number", num)
        p.put("begin", 0)
        val data = QqClient.cgi("musichall.song_list_server", "GetSingerSongList", p)
        val arr = data.optJSONArray("song_list") ?: data.optJSONArray("songlist") ?: return emptyList()
        val out = ArrayList<MusicApi.Song>()
        for (i in 0 until arr.length()) MusicApi.parseSong(arr.optJSONObject(i))?.let { out.add(it) }
        return out
    }
}
