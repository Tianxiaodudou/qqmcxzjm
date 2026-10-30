package com.tianxiaodudou.qqmusicdownloader.net

import android.content.Context

/** QQ 音乐登录凭证：uin 是 musicid，musickey 既是 cookie 也是 g_tk 的来源。 */
data class Session(val uin: String, val musickey: String, val nickname: String) {

    companion object {
        private const val PREFS = "qy_session"
        private const val K_UIN = "uin"
        private const val K_KEY = "musickey"
        private const val K_NICK = "nickname"

        fun load(ctx: Context): Session? {
            val p = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            val uin = p.getString(K_UIN, null) ?: return null
            val key = p.getString(K_KEY, null) ?: return null
            if (key.isEmpty()) return null
            return Session(uin, key, p.getString(K_NICK, "").orEmpty())
        }

        fun save(ctx: Context, s: Session) {
            ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putString(K_UIN, s.uin)
                .putString(K_KEY, s.musickey)
                .putString(K_NICK, s.nickname)
                .apply()
            QqClient.session = s
        }

        fun clear(ctx: Context) {
            ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().clear().apply()
            QqClient.session = null
        }
    }
}
