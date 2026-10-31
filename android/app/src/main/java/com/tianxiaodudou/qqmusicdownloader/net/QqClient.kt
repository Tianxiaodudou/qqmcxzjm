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

    private var store: CookieStore? = null

    fun init(context: Context) {
        if (::http.isInitialized) return
        val jar = CookieStore(context.applicationContext)
        store = jar
        http = OkHttpClient.Builder()
            .cookieJar(jar)
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(25, TimeUnit.SECONDS)
            .writeTimeout(25, TimeUnit.SECONDS)
            .build()
    }

    /** 设备 guid：QQ 接口用来标识一次播放请求，随便生成一次固定即可。 */
    val guid: String by lazy {
        val bytes = ByteArray(16)
        java.util.Random().nextBytes(bytes)
        bytes.joinToString("") { String.format("%02X", it) }
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

    /**
     * 不带任何已有 cookie 的请求。ptlogin 的 check_sig 必须这样发：
     * jar 里残留的 qrsig / uin 会让服务端 302 但不下发 p_skey。
     * 响应里的 Set-Cookie 仍写回主 jar，供后续 oauth2 使用。
     */
    fun getAnonymous(url: String, headers: Map<String, String>, followRedirects: Boolean = false): okhttp3.Response {
        val jar = store
        val client = http.newBuilder()
            .followRedirects(followRedirects).followSslRedirects(followRedirects)
            .cookieJar(object : okhttp3.CookieJar {
                override fun saveFromResponse(u: okhttp3.HttpUrl, cookies: List<okhttp3.Cookie>) {
                    jar?.saveFromResponse(u, cookies)
                }
                override fun loadForRequest(u: okhttp3.HttpUrl): List<okhttp3.Cookie> = emptyList()
            })
            .build()
        val b = Request.Builder().url(url).header("User-Agent", UA)
        for ((k, v) in headers) b.header(k, v)
        return client.newCall(b.build()).execute()
    }

    /** 从持久化 jar 里取某个 cookie 的值（登录换 key 时要用 p_skey / skey）。 */
    fun cookie(name: String): String? = store?.value(name)

    fun post(url: String, body: okhttp3.RequestBody, headers: Map<String, String>): okhttp3.Response {
        val b = Request.Builder().url(url).post(body).header("User-Agent", UA)
        for ((k, v) in headers) b.header(k, v)
        val client = http.newBuilder().followRedirects(false).followSslRedirects(false).build()
        return client.newCall(b.build()).execute()
    }

    fun get(url: String, headers: Map<String, String>, followRedirects: Boolean = true): okhttp3.Response {
        val b = Request.Builder().url(url).header("User-Agent", UA)
        // 默认 Referer 只在不覆盖调用方的时候补，否则 ptlogin 这类接口会被改成 y.qq.com 而失败
        if (!headers.keys.any { it.equals("Referer", true) }) b.header("Referer", "https://y.qq.com/")
        for ((k, v) in headers) b.header(k, v)
        val req = b.build()
        val client = if (followRedirects) http else http.newBuilder().followRedirects(false).followSslRedirects(false).build()
        return client.newCall(req).execute()
    }
}

class QqError(message: String) : Exception(message)
