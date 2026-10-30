package com.tianxiaodudou.qqmusicdownloader.net

import android.content.Context
import java.io.File

/** 本地设置：音质偏好、缓存清理。 */
object Settings {

    enum class Quality(val label: String) {
        AUTO("优先无损（够会员时听 SQ）"),
        STANDARD("只播标准/高品质（省流量）"),
    }

    private const val PREFS = "settings"
    private const val K_QUALITY = "quality"

    fun quality(context: Context): Quality {
        val raw = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(K_QUALITY, null)
        return if (raw == "standard") Quality.STANDARD else Quality.AUTO
    }

    fun setQuality(context: Context, q: Quality) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(K_QUALITY, if (q == Quality.AUTO) "auto" else "standard").apply()
    }

    /** 清掉无损解密缓存与封面临时文件，返回释放的字节数。 */
    fun clearCache(context: Context): Long {
        var freed = 0L
        val dirs = listOf(File(context.cacheDir, "lossless"), context.cacheDir)
        for (dir in dirs) {
            val files = dir.listFiles() ?: continue
            for (f in files) {
                if (dir == context.cacheDir && !f.name.endsWith(".jpg") && !f.name.endsWith(".mp3") && !f.name.endsWith(".mflac")) continue
                freed += f.length()
                f.delete()
            }
        }
        return freed
    }
}
