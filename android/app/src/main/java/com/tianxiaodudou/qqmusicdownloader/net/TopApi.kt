package com.tianxiaodudou.qqmusicdownloader.net

import org.json.JSONObject

/** 排行榜：分类列表 + 榜单歌曲。 */
object TopApi {

    data class Chart(val id: Int, val name: String, val count: Int)

    fun charts(): List<Chart> {
        val data = QqClient.cgi("music.musicToplist.Toplist", "GetAll", JSONObject())
        val arr = data.optJSONArray("toplist") ?: data.optJSONArray("v_list") ?: return emptyList()
        val out = ArrayList<Chart>()
        for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            val id = o.optInt("topId", o.optInt("topID", 0))
            if (id <= 0) continue
            out.add(
                Chart(
                    id = id,
                    name = o.optString("topName").ifEmpty { o.optString("title") },
                    count = o.optInt("totalNum", o.optInt("songNum", 0)),
                )
            )
        }
        return out
    }

    fun songs(chart: Chart, page: Int = 1, num: Int = 100): List<MusicApi.Song> {
        val p = JSONObject()
        p.put("topId", chart.id)
        p.put("offset", num * (page - 1))
        p.put("num", num)
        p.put("withTags", true)
        val data = QqClient.cgi("music.musicToplist.Toplist", "GetDetail", p)
        val arr = data.optJSONArray("songInfoList") ?: data.optJSONArray("songlist") ?: return emptyList()
        val out = ArrayList<MusicApi.Song>()
        for (i in 0 until arr.length()) MusicApi.parseSong(arr.optJSONObject(i))?.let { out.add(it) }
        return out
    }
}
