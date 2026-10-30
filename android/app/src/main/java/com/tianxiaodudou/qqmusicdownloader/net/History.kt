package com.tianxiaodudou.qqmusicdownloader.net

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/** 下载历史：落在本地，最多保留 200 条。 */
object History {

    data class Item(val title: String, val artist: String, val path: String, val time: Long)

    private const val PREFS = "download_history"
    private const val KEY = "items"
    private const val MAX = 200

    fun list(context: Context): List<Item> {
        val raw = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, null) ?: return emptyList()
        return runCatching {
            val arr = JSONArray(raw)
            (0 until arr.length()).mapNotNull { i ->
                val o = arr.optJSONObject(i) ?: return@mapNotNull null
                Item(
                    title = o.optString("title"),
                    artist = o.optString("artist"),
                    path = o.optString("path"),
                    time = o.optLong("time"),
                )
            }
        }.getOrElse { emptyList() }
    }

    @Synchronized
    fun add(context: Context, song: MusicApi.Song, path: String) {
        val items = ArrayList(list(context))
        items.add(0, Item(song.name, song.singers, path, System.currentTimeMillis()))
        while (items.size > MAX) items.removeAt(items.size - 1)
        val arr = JSONArray()
        for (it in items) {
            arr.put(
                JSONObject()
                    .put("title", it.title)
                    .put("artist", it.artist)
                    .put("path", it.path)
                    .put("time", it.time)
            )
        }
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY, arr.toString()).apply()
    }

    fun clear(context: Context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().remove(KEY).apply()
    }
}
