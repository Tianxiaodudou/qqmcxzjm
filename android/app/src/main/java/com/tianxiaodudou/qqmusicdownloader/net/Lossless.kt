package com.tianxiaodudou.qqmusicdownloader.net

import android.content.Context
import okhttp3.Request
import java.io.File

/** 无损音源要整首下完再解密，缓存到应用私有目录，同一首只解一次。 */
object Lossless {

    fun playableFile(context: Context, song: MusicApi.Song): File? {
        if (Settings.quality(context) == Settings.Quality.STANDARD) return null
        val cached = File(cacheDir(context), song.mid + ".flac")
        if (cached.exists() && cached.length() > 1024) return cached
        val pair = MusicApi.lossless(song) ?: return null
        val key = Qmc2.deriveKey(pair.second) ?: return null

        val tmp = File(cacheDir(context), song.mid + ".mflac")
        val request = Request.Builder().url(pair.first).header("User-Agent", QqClient.userAgent()).build()
        QqClient.http.newCall(request).execute().use { resp ->
            val body = resp.body ?: return null
            if (!resp.isSuccessful) return null
            tmp.outputStream().use { out -> body.byteStream().copyTo(out) }
        }
        val raw = tmp.readBytes()
        if (raw.size < 1024) { tmp.delete(); return null }
        val head = String(raw, 0, 4, Charsets.ISO_8859_1)
        val data = if (head == "fLaC") raw else Qmc2.decrypt(raw, key, 0).let { raw }
        cached.writeBytes(data)
        tmp.delete()
        return if (cached.length() > 1024) cached else null
    }

    private fun cacheDir(context: Context): File {
        val dir = File(context.cacheDir, "lossless")
        if (!dir.exists()) dir.mkdirs()
        return dir
    }
}
