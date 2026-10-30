package com.tianxiaodudou.qqmusicdownloader.net

import org.json.JSONObject

/** 首页推荐：目前接「猜你喜欢」。 */
object RecommendApi {

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
}
