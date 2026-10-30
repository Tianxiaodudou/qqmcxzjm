package com.tianxiaodudou.qqmusicdownloader.net

import android.content.Context
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** QQ 音乐接口基座：统一的 comm 参数 + 单请求转发。 */
object QqClient {

    private const val MUSICU = "https://u.y.qq.com/cgi-bin/musicu.fcg"
    private const val UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    private val JSON_TYPE = "application/json; charset=utf-8".toMediaType()

    lateinit var http: OkHttpClient
        private set

    @Volatile
    var session: Session? = null

    fun init(context: Context) {
        if (::http.isInitialized) return
        http = OkHttpClient.Builder()
            .cookieJar(CookieStore(context.applicationContext))
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(25, TimeUnit.SECONDS)
            .writeTimeout(25, TimeUnit.SECONDS)
            .build()
    }

    fun hash33(s: String, seed: Int = 0): Int {
        var h = seed
        for (ch in s) h = (h shl 5) + h + ch.code
        return h and 0x7fffffff
    }

    fun gtk(): Int = session?.musickey?.takeIf { it.isNotEmpty() }?.let { hash33(it, 5381) } ?: 5381

    fun userAgent(): String = UA

    /** 组装 comm。字段与 web 平台一致，字段名不要改。 */
    fun comm(extra: Map<String, String> = emptyMap()): JSONObject {
        val c = JSONObject()
        c.put("ct", "24")
        c.put("cv", "4747474")
        c.put("platform", "yqq.json")
        c.put("chid", "0")
        c.put("uin", session?.uin ?: "0")
        c.put("g_tk", gtk().toString())
        c.put("g_tk_new_20200303", gtk().toString())
        c.put("format", "json")
        c.put("inCharset", "utf-8")
        c.put("outCharset", "utf-8")
        c.put("notice", "0")
        c.put("need_new_code", "1")
        for ((k, v) in extra) c.put(k, v)
        return c
    }

    /**
     * 调一次 musicu.fcg，返回 req_0.data。
     * 上游 code 非 0 时抛 [QqError]，带上游 message 方便排查。
     */
    fun cgi(module: String, method: String, param: JSONObject, extraComm: Map<String, String> = emptyMap()): JSONObject {
        val body = JSONObject()
        body.put("comm", comm(extraComm))
        body.put("req_0", JSONObject().put("module", module).put("method", method).put("param", param))
        val request = Request.Builder()
            .url(MUSICU)
            .post(body.toString().toRequestBody(JSON_TYPE))
            .header("User-Agent", UA)
            .header("Referer", "https://y.qq.com/")
            .build()
        http.newCall(request).execute().use { resp ->
            val text = resp.body?.string() ?: throw QqError("接口无响应 (" + resp.code + ")")
            val root = JSONObject(text)
            val sub = root.optJSONObject("req_0")
            if (sub == null) {
                val msg = root.optString("errMsg", text.take(120))
                throw QqError("接口返回异常：" + msg)
            }
            val code = sub.optInt("code", -1)
            if (code != 0) {
                throw QqError("接口错误 " + code + "：" + sub.optString("msg", sub.optString("errMsg", "")))
            }
            return sub.optJSONObject("data") ?: JSONObject()
        }
    }

    fun post(url: String, body: okhttp3.RequestBody, headers: Map<String, String>): okhttp3.Response {
        val b = Request.Builder().url(url).post(body).header("User-Agent", UA)
        for ((k, v) in headers) b.header(k, v)
        val client = http.newBuilder().followRedirects(false).followSslRedirects(false).build()
        return client.newCall(b.build()).execute()
    }

    fun get(url: String, headers: Map<String, String>, followRedirects: Boolean = true): okhttp3.Response {
        val b = Request.Builder().url(url).header("User-Agent", UA)
        for ((k, v) in headers) b.header(k, v)
        b.header("Referer", "https://y.qq.com/")
        val req = b.build()
        val client = if (followRedirects) http else http.newBuilder().followRedirects(false).followSslRedirects(false).build()
        return client.newCall(req).execute()
    }
}

class QqError(message: String) : Exception(message)
