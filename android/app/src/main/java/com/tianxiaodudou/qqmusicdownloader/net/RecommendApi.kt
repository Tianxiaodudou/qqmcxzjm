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
        val pic: String,
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
                    pic = b.optJSONObject("cover")?.optString("default_url").orEmpty()
                        .ifEmpty { b.optString("picurl") }
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

    data class FeedCard(val kind: String, val id: String, val name: String, val sub: String, val cover: String)
    data class FeedGroup(val title: String, val cards: List<FeedCard>)

    /** 首页信息流（shelves -> niches -> cards），对应 fpk 的 RecommendFeed / get_recommend_feed。 */
    fun homeFeed(page: Int = 1, limit: Int = 30): List<FeedGroup> {
        val p = JSONObject()
        p.put("direction", 0)
        p.put("page", page)
        p.put("s_num", 0)
        p.put("v_cache", org.json.JSONArray())
        val data = QqClient.cgi("music.recommend.RecommendFeed", "get_recommend_feed", p)
        val shelves = data.optJSONArray("v_shelf") ?: return emptyList()
        val out = ArrayList<FeedGroup>()
        for (i in 0 until shelves.length()) {
            val sh = shelves.optJSONObject(i) ?: continue
            var title = sh.optString("title_content").ifEmpty { sh.optString("title_template") }
            if (title.isEmpty() || title.startsWith("{")) title = "为你推荐"
            val cards = ArrayList<FeedCard>()
            val niches = sh.optJSONArray("v_niche")
            if (niches != null) {
                for (j in 0 until niches.length()) {
                    val nc = niches.optJSONObject(j) ?: continue
                    val arr = nc.optJSONArray("v_card") ?: continue
                    for (k in 0 until arr.length()) {
                        val raw = arr.optJSONObject(k) ?: continue
                        cardOf(raw)?.let { cards.add(it) }
                    }
                }
            }
            if (cards.isNotEmpty()) {
                out.add(FeedGroup(title, cards.take(limit)))
                if (out.size >= 2) break
            }
        }
        return out
    }

    private fun cardOf(raw: JSONObject): FeedCard? {
        val id = raw.optString("id")
        val title = raw.optString("title")
        if (id.isEmpty() || title.isEmpty()) return null
        val type = raw.optInt("type")
        val module = raw.optJSONObject("extra_info")?.optString("moduleID").orEmpty()
        val sub = raw.optString("subtitle")
        val cover = raw.optString("cover")
        return when {
            module.startsWith("playlist") || type == 500 -> FeedCard("songlist", id, title, sub, cover)
            type == 200 -> FeedCard("song", id, title, sub, cover)
            else -> null
        }
    }

    /** 每日推荐（私人雷达），对应 fpk 的 TrackRelationServer / GetRadarSong。 */
    fun radar(page: Int = 1): List<MusicApi.Song> {
        val p = JSONObject()
        p.put("Page", page)
        p.put("ReqType", 0)
        p.put("FavSongs", org.json.JSONArray())
        p.put("EntranceSongs", org.json.JSONArray())
        val data = QqClient.cgi("music.recommend.TrackRelationServer", "GetRadarSong", p)
        val arr = data.optJSONArray("VecSongs") ?: return emptyList()
        val out = ArrayList<MusicApi.Song>()
        for (i in 0 until arr.length()) {
            val row = arr.optJSONObject(i) ?: continue
            val track = row.optJSONObject("Track") ?: row
            MusicApi.parseSong(track)?.let { out.add(it) }
        }
        return out
    }
}
