package com.tianxiaodudou.qqmusicdownloader.net

import android.content.Context
import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.HttpUrl
import org.json.JSONArray
import org.json.JSONObject

/**
 * 简单的持久化 Cookie 存储：QQ 音乐整套登录态都是 cookie + musickey，
 * 进程重启后要能接着用，所以落 SharedPreferences。
 */
class CookieStore(context: Context) : CookieJar {

    private val prefs = context.getSharedPreferences("cookies", Context.MODE_PRIVATE)
    private val cache = LinkedHashMap<String, Cookie>()

    init {
        prefs.getString(KEY, null)?.let { raw ->
            runCatching {
                val arr = JSONArray(raw)
                for (i in 0 until arr.length()) {
                    val o = arr.getJSONObject(i)
                    Cookie.parse(HttpUrl.Builder().scheme("https").host(o.getString("domain").removePrefix(".")).build(), o.getString("cookie"))
                        ?.let { cache[it.domain + "|" + it.name + "|" + it.path] = it }
                }
            }
        }
    }

    @Synchronized
    override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) {
        for (c in cookies) {
            if (c.value.isEmpty()) cache.remove(c.domain + "|" + c.name + "|" + c.path)
            else cache[c.domain + "|" + c.name + "|" + c.path] = c
        }
        persist()
    }

    @Synchronized
    override fun loadForRequest(url: HttpUrl): List<Cookie> =
        cache.values.filter { it.matches(url) && it.expiresAt > System.currentTimeMillis() }

    @Synchronized
    fun value(name: String): String? =
        cache.values.firstOrNull { it.name == name && it.expiresAt > System.currentTimeMillis() }?.value

    @Synchronized
    fun clear() {
        cache.clear()
        prefs.edit().remove(KEY).apply()
    }

    private fun persist() {
        val arr = JSONArray()
        for (c in cache.values) {
            arr.put(JSONObject().put("domain", c.domain).put("cookie", c.toString()))
        }
        prefs.edit().putString(KEY, arr.toString()).apply()
    }

    private companion object {
        const val KEY = "jar"
    }
}
