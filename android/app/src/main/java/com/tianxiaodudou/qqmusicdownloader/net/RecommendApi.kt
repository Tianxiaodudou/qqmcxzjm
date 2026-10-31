package com.tianxiaodudou.qqmusicdownloader.net

import org.json.JSONObject

/** 首页推荐：猜你喜欢 / 推荐歌单 / 新歌速递。 */
object RecommendApi {

    data class Songlist(
        val id: String,
        val dirId: String,
        val name: String,
        val songNum: Int,
        val listenNum: Int,
        val creator: String,
    )

    fun guess(num: Int = 30): List<MusicApi.Song> {
        val p = JSONObject()
        p.put("id", 99)
        p.put("num", num)
        p.put("from", 0)
        p.put("scene", 0)
        p.put("song_ids", org.json.JSONArray())
        val data = QqClient.cgi("music.radioProxy.MbTrackRadioSvr", "get_radio_track", p)
        val arr = data.optJSONArray("tracks")
            ?: data.optJSONArray("songlist")
            ?: data.optJSONArray("v_songlist")
            ?: data.optJSONArray("vecSong")
            ?: return emptyList()
        val out = ArrayList<MusicApi.Song>()
        for (i in 0 until arr.length()) MusicApi.parseSong(arr.optJSONObject(i))?.let { out.add(it) }
        return out
    }

    /** 推荐歌单，对应 fpk 的 music.playlist.PlaylistSquare / GetRecommendFeed。 */
    fun songlists(page: Int = 1, num: Int = 20): List<Songlist> {
        val p = JSONObject()
        p.put("From", num * (page - 1))
        p.put("Size", num)
        val data = QqClient.cgi("music.playlist.PlaylistSquare", "GetRecommendFeed", p)
        val arr = data.optJSONArray("List") ?: return emptyList()
        val out = ArrayList<Songlist>()
        for (i in 0 until arr.length()) {
            val row = arr.optJSONObject(i) ?: continue
            val pl = row.optJSONObject("Playlist") ?: continue
            val b = pl.optJSONObject("basic") ?: pl
            val id = b.optString("tid").ifEmpty { b.optString("dissid").ifEmpty { b.optString("id") } }
            if (id.isEmpty()) continue
            out.add(
                Songlist(
                    id = id,
                    dirId = b.optString("dirId").ifEmpty { b.optString("dirid") },
                    name = b.optString("title").ifEmpty { b.optString("name") },
                    songNum = b.optInt("song_cnt", b.optInt("songnum", 0)),
                    listenNum = b.optInt("play_cnt", b.optInt("listennum", 0)),
                    creator = b.optJSONObject("creator")?.optString("nick").orEmpty(),
                )
            )
        }
        return out
    }

    /** 新歌速递，type: 1内地 2欧美 3日本 4韩国 5最新 6港台。 */
    fun newSongs(type: Int = 5): List<MusicApi.Song> {
        val p = JSONObject().put("type", type)
        val data = QqClient.cgi("newsong.NewSongServer", "get_new_song_info", p)
        val arr = data.optJSONArray("songlist") ?: data.optJSONArray("songs") ?: return emptyList()
        val out = ArrayList<MusicApi.Song>()
        for (i in 0 until arr.length()) MusicApi.parseSong(arr.optJSONObject(i))?.let { out.add(it) }
        return out
    }
}
