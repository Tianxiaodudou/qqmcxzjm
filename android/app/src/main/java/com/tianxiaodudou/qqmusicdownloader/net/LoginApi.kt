package com.tianxiaodudou.qqmusicdownloader.net

import okhttp3.FormBody
import org.json.JSONObject
import kotlin.random.Random

/**
 * QQ 扫码登录（与网页端/官方客户端同一套 ptlogin 流程）：
 * 取二维码 -> 轮询扫码状态 -> check_sig 换 p_skey -> oauth2 授权换 code -> QQLogin 换 musickey。
 */
object LoginApi {

    private const val APPID = "716027609"
    private const val THIRD_AID = "100497308"
    private const val REFERER = "https://xui.ptlogin2.qq.com/"

    class Qr(val image: ByteArray, val qrsig: String)

    sealed interface State {
        data object Waiting : State
        data object Scanned : State
        data object Expired : State
        data object Cancelled : State
        class Failed(val message: String) : State
        class LoggedIn(val session: Session) : State
    }

    @Volatile
    private var qrsig: String = ""

    fun qrShow(): Qr {
        val url = "https://ssl.ptlogin2.qq.com/ptqrshow" +
            "?appid=" + APPID +
            "&e=2&l=M&s=3&d=72&v=4" +
            "&t=" + Random.nextDouble() +
            "&daid=383&pt_3rd_aid=" + THIRD_AID
        QqClient.get(url, mapOf("Referer" to REFERER)).use { resp ->
            val body = resp.body?.bytes() ?: throw QqError("二维码下载失败")
            var sig = ""
            for (h in resp.headers("Set-Cookie")) {
                val i = h.indexOf("qrsig=")
                if (i >= 0) sig = h.substring(i + 6).substringBefore(';')
            }
            if (sig.isEmpty()) throw QqError("未拿到 qrsig，二维码无效")
            qrsig = sig
            return Qr(body, sig)
        }
    }

    fun poll(): State {
        val token = QqClient.hash33(qrsig)
        val url = "https://ssl.ptlogin2.qq.com/ptqrlogin" +
            "?u1=" + enc("https://graph.qq.com/oauth2.0/login_jump") +
            "&ptqrtoken=" + token +
            "&ptredirect=0&h=1&t=1&g=1&from_ui=1&ptlang=2052" +
            "&action=0-0-" + System.currentTimeMillis() +
            "&js_ver=20102616&js_type=1&pt_uistyle=40" +
            "&aid=" + APPID + "&daid=383&pt_3rd_aid=" + THIRD_AID + "&has_onekey=1"
        val text = QqClient.get(url, mapOf("Referer" to REFERER)).use { it.body?.string().orEmpty() }
        // 与 fpk 一样先取出 ptuiCB(...) 整体，再按单引号切参数：
        // 严格匹配 6 段的正则在昵称带引号、或参数间有空格时会整体失配，
        // 异常被上层吃掉后就会永远停在「已扫码」。
        val body = Regex("""ptuiCB\((.*?)\)""").find(text)?.groupValues?.get(1)
            ?: throw QqError("无法解析扫码状态")
        val args = Regex("""'(.*?)'""").findAll(body).map { it.groupValues[1] }.toList()
        if (args.size < 5) throw QqError("扫码状态参数不完整")
        val code = args[0]
        val redirect = args[2]
        val nick = if (args.size > 5) args[5] else ""
        return when (code) {
            "0" -> try {
                State.LoggedIn(authorize(redirect, nick))
            } catch (e: Exception) {
                State.Failed(e.message ?: "换取登录态的某一步失败了")
            }
            "1", "67" -> State.Scanned
            "2", "65" -> State.Expired
            "3" -> State.Cancelled
            "66" -> State.Waiting
            else -> State.Waiting
        }
    }

    private fun authorize(redirect: String, nick: String): Session {
        val query = redirect.substringAfter('?', "")
        val ptsigx = param(query, "ptsigx") ?: throw QqError("缺少 ptsigx")
        val uin = param(query, "uin") ?: throw QqError("缺少 uin")

        val checkUrl = "https://ssl.ptlogin2.graph.qq.com/check_sig" +
            "?uin=" + uin +
            "&pttype=1&service=ptqrlogin&nodirect=0" +
            "&ptsigx=" + ptsigx +
            "&s_url=" + enc("https://graph.qq.com/oauth2.0/login_jump") +
            "&ptlang=2052&ptredirect=100&aid=" + APPID + "&daid=383" +
            "&j_later=0&low_login_hour=0&regmaster=0&pt_login_type=3" +
            "&pt_aid=0&pt_aaid=16&pt_light=0&pt_3rd_aid=" + THIRD_AID
        var pSkey = ""
        var status = 0
        QqClient.get(checkUrl, mapOf("Referer" to REFERER), followRedirects = false).use { resp ->
            status = resp.code
            for (h in resp.headers("Set-Cookie")) {
                val i = h.indexOf("p_skey=")
                if (i >= 0) pSkey = h.substring(i + 7).substringBefore(';')
            }
        }
        if (pSkey.isEmpty()) throw QqError("check_sig 返回 " + status + " 但没有 p_skey（授权链接可能已过期，重新扫码试试）")

        val form = FormBody.Builder()
            .add("response_type", "code")
            .add("client_id", THIRD_AID)
            .add("redirect_uri", "https://y.qq.com/portal/wx_redirect.html?login_type=1&surl=https://y.qq.com/")
            .add("scope", "get_user_info,get_app_friends")
            .add("state", "state")
            .add("switch", "")
            .add("from_ptlogin", "1")
            .add("src", "1")
            .add("update_auth", "1")
            .add("openapi", "1010_1030")
            .add("g_tk", QqClient.hash33(pSkey, 5381).toString())
            .add("auth_time", (System.currentTimeMillis()).toString())
            .add("ui", java.util.UUID.randomUUID().toString())
            .build()
        var code = ""
        var oauthStatus = 0
        QqClient.post("https://graph.qq.com/oauth2.0/authorize", form, mapOf("Referer" to REFERER)).use { resp ->
            oauthStatus = resp.code
            val location = resp.header("Location").orEmpty()
            val i = location.indexOf("code=")
            if (i >= 0) code = location.substring(i + 5).substringBefore('&')
        }
        if (code.isEmpty()) throw QqError("oauth2 授权返回 " + oauthStatus + "，没拿到 code")

        val data = QqClient.cgi(
            "QQConnectLogin.LoginServer",
            "QQLogin",
            JSONObject().put("code", code),
            mapOf("tmeLoginType" to "2"),
        )
        val musicid = pick(data, "musicid", "musicId", "uin", "strMusicid") ?: throw QqError("登录返回缺少 musicid")
        val musickey = pick(data, "musickey", "musicKey", "qm_keyst") ?: throw QqError("登录返回缺少 musickey")
        return Session(musicid, musickey, nick)
    }


    // ---------------- 微信扫码 ----------------

    class WxQr(val image: ByteArray, val uuid: String)

    fun wxQrShow(): WxQr {
        val url = "https://open.weixin.qq.com/connect/qrconnect" +
            "?appid=wx48db31d50e334801" +
            "&redirect_uri=" + enc("https://y.qq.com/portal/wx_redirect.html?login_type=2&surl=https://y.qq.com/") +
            "&response_type=code&scope=snsapi_login&state=STATE" +
            "&href=" + enc("https://y.qq.com/mediastyle/music_v17/src/css/popup_wechat.css#wechat_redirect")
        val html = QqClient.get(url, mapOf("Referer" to "https://y.qq.com/")).use { it.body?.string().orEmpty() }
        // 页面里 uuid 会出现在 ?uuid=xxx 和 /connect/qrcode/xxx 两处，优先按路径取
        val uuid = Regex("""/connect/qrcode/([0-9a-zA-Z_-]{10,})""").find(html)?.groupValues?.get(1)
            ?: Regex("""uuid=([0-9a-zA-Z_-]{10,})""").find(html)?.groupValues?.get(1)
            ?: throw QqError("获取微信 uuid 失败")
        val img = QqClient.get(
            "https://open.weixin.qq.com/connect/qrcode/" + uuid,
            mapOf("Referer" to "https://open.weixin.qq.com/connect/qrconnect"),
        ).use { it.body?.bytes() ?: throw QqError("微信二维码下载失败") }
        return WxQr(img, uuid)
    }

    fun wxPoll(uuid: String): State {
        val url = "https://lp.open.weixin.qq.com/connect/l/qrconnect?uuid=" + uuid + "&_=" + System.currentTimeMillis()
        val text = QqClient.get(url, mapOf("Referer" to "https://open.weixin.qq.com/")).use { it.body?.string().orEmpty() }
        val err = Regex("""wx_errcode\s*=\s*(\d+)""").find(text)?.groupValues?.get(1) ?: return State.Waiting
        val code = Regex("""wx_code\s*=\s*'([^']*)'""").find(text)?.groupValues?.get(1).orEmpty()
        return when (err) {
            "405" -> {
                if (code.isEmpty()) State.Waiting else State.LoggedIn(wxAuthorize(code))
            }
            "404" -> State.Scanned
            "403" -> State.Expired
            else -> State.Waiting
        }
    }

    private fun wxAuthorize(code: String): Session {
        val data = QqClient.cgi(
            "music.login.LoginServer",
            "Login",
            JSONObject().put("code", code).put("strAppid", "wx48db31d50e334801"),
            mapOf("tmeLoginType" to "1"),
        )
        return toSession(data, "微信用户")
    }

    // ---------------- 手机验证码 ----------------

    fun sendSms(phone: String): String {
        val p = JSONObject().put("tmeAppid", "qqmusic").put("areaCode", "86").put("phoneNo", phone)
        return try {
            QqClient.cgi("music.login.LoginServer", "SendPhoneAuthCode", p, mapOf("tmeLoginMethod" to "3"))
            "验证码已发送"
        } catch (e: Exception) {
            val msg = e.message.orEmpty()
            when {
                msg.contains("20276") -> "这台设备需要先过滑块验证：请用手机浏览器打开 y.qq.com 登录一次，再回来发验证码"
                msg.contains("100001") -> "发送太频繁，过几分钟再试"
                else -> "发送失败：" + msg
            }
        }
    }

    fun smsLogin(phone: String, code: String): Session {
        val p = JSONObject().put("code", code).put("loginMode", 1).put("phoneNo", phone)
        val data = QqClient.cgi(
            "music.login.LoginServer",
            "Login",
            p,
            mapOf("tmeLoginMethod" to "3", "tmeLoginType" to "0"),
        )
        return toSession(data, "手机用户")
    }

    private fun toSession(data: JSONObject, fallbackNick: String): Session {
        val musicid = pick(data, "musicid", "musicId", "uin") ?: throw QqError("登录返回缺少 musicid")
        val musickey = pick(data, "musickey", "musicKey", "qm_keyst") ?: throw QqError("登录返回缺少 musickey")
        return Session(musicid, musickey, fallbackNick)
    }

    private fun pick(o: JSONObject, vararg keys: String): String? {
        for (k in keys) {
            val v = o.opt(k)
            if (v != null && v != JSONObject.NULL && v.toString().isNotEmpty() && v.toString() != "0") return v.toString()
        }
        for (nested in listOf("result", "data", "baseInfo", "creds")) {
            val sub = o.optJSONObject(nested) ?: continue
            pick(sub, *keys)?.let { return it }
        }
        return null
    }

    private fun param(query: String, key: String): String? {
        for (pair in query.split('&')) {
            val i = pair.indexOf('=')
            if (i > 0 && pair.substring(0, i) == key) return pair.substring(i + 1)
        }
        return null
    }

    private fun enc(s: String): String = java.net.URLEncoder.encode(s, "UTF-8")
}
