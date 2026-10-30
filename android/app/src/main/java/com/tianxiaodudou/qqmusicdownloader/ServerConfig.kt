package com.tianxiaodudou.qqmusicdownloader

import android.content.Context
import android.net.Uri

/**
 * 服务器地址的持久化与规范化。
 *
 * 本应用是飞牛 NAS 上那套服务端的客户端：所有接口、解密、下载都在 NAS 上完成，
 * APK 只负责把同一个 Web 界面装进 WebView。用户只需要填一次 NAS 地址。
 */
object ServerConfig {

    private const val PREFS = "qqmusic_client"
    private const val KEY_SERVER = "server_url"

    /** 飞牛网关给这个应用分配的前缀路径（见 manifest 的 desktop_uidir）。 */
    const val APP_PATH = "/app/qqmusic-downloader/"

    fun getServer(context: Context): String? =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_SERVER, null)

    fun setServer(context: Context, url: String?) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().apply {
            if (url.isNullOrBlank()) remove(KEY_SERVER) else putString(KEY_SERVER, url)
        }.apply()
    }

    /** 把用户随手输入的 "192.168.1.10:5666" 补全成可直接加载的完整地址；无效返回 null。 */
    fun normalize(raw: String): String? {
        var text = raw.trim()
        if (text.isEmpty()) return null
        if (!text.startsWith("http://", true) && !text.startsWith("https://", true)) {
            text = "http://" + text
        }
        val uri = Uri.parse(text)
        val host = uri.host
        if (host.isNullOrBlank()) return null

        val scheme = (uri.scheme ?: "http").lowercase()
        var path = uri.path.orEmpty()
        if (path.isBlank() || path == "/") path = APP_PATH
        if (!path.endsWith("/")) path = path + "/"

        val port = if (uri.port > 0) ":" + uri.port else ""
        return scheme + "://" + host + port + path
    }
}
