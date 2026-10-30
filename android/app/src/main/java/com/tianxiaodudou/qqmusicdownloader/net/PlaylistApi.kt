package com.tianxiaodudou.qqmusicdownloader.net

import org.json.JSONObject

/** 歌单：我的歌单列表 + 歌单里的歌曲。 */
object PlaylistApi {

    data class Item(val id: String, val dirId: String, val name: String, val count: Int)

    fun mine(): List<Item> {
        val uin = QqClient.session?.uin ?: return emptyList()
        val data = QqClient.cgi("music.musicasset.PlaylistBaseRead", "GetPlaylistByUin", JSONObject().put("uin", uin))
        val arr = data.optJSONArray("v_playlist")
            ?: data.optJSONArray("playlists")
            ?: data.optJSONArray("content")
            ?: return emptyList()
        val out = ArrayList<Item>()
        for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            val id = o.optString("tid").ifEmpty { o.optString("disstid") }
            if (id.isEmpty()) continue
            out.add(
                Item(
                    id = id,
                    dirId = o.optString("dirId").ifEmpty { o.optString("dirid") },
                    name = o.optString("dirName").ifEmpty { o.optString("name") },
                    count = o.optInt("songNum", o.optInt("songnum", 0)),
                )
            )
        }
        return out
    }

    fun songs(item: Item, page: Int = 1, num: Int = 100): List<MusicApi.Song> {
        val p = JSONObject()
        p.put("disstid", item.id)
        p.put("dirid", item.dirId.ifEmpty { "0" })
        p.put("tag", false)
        p.put("song_begin", num * (page - 1))
        p.put("song_num", num)
        p.put("userinfo", false)
        p.put("orderlist", true)
        p.put("onlysonglist", false)
        val data = QqClient.cgi("music.srfDissInfo.DissInfo", "CgiGetDiss", p)
        val arr = data.optJSONArray("songlist") ?: return emptyList()
        val out = ArrayList<MusicApi.Song>()
        for (i in 0 until arr.length()) MusicApi.parseSong(arr.optJSONObject(i))?.let { out.add(it) }
        return out
    }
}
