package com.tianxiaodudou.qqmusicdownloader.net

import android.content.Context
import okhttp3.Request
import org.jaudiotagger.audio.AudioFileIO
import org.jaudiotagger.tag.FieldKey
import org.jaudiotagger.tag.images.ArtworkFactory
import java.io.File

/** 把歌名/歌手/专辑/封面/歌词写进音频文件本身，系统与第三方播放器都能看到。 */
object Tagger {

    fun tag(context: Context, file: File, song: MusicApi.Song, lyrics: String = "") {
        runCatching {
            val audio = AudioFileIO.read(file)
            val tag = audio.tagOrCreateAndSetDefault
            tag.setField(FieldKey.TITLE, song.name)
            if (song.singers.isNotEmpty()) tag.setField(FieldKey.ARTIST, song.singers)
            if (song.album.isNotEmpty()) tag.setField(FieldKey.ALBUM, song.album)
            tag.setField(FieldKey.COMMENT, "qqmusic " + song.mid)
            if (lyrics.isNotEmpty()) runCatching { tag.setField(FieldKey.LYRICS, lyrics) }

            val cover = coverFile(context, song)
            if (cover != null) {
                runCatching {
                    tag.deleteArtworkField()
                    tag.setField(ArtworkFactory.createArtworkFromFile(cover))
                }
                cover.delete()
            }
            audio.commit()
        }
    }

    private fun coverFile(context: Context, song: MusicApi.Song): File? {
        if (song.albumMid.isEmpty()) return null
        val url = "https://y.gtimg.cn/music/photo_new/T002R500x500M000" + song.albumMid + ".jpg"
        val dst = File(context.cacheDir, "cover_" + song.mid + ".jpg")
        return runCatching {
            val req = Request.Builder().url(url).header("User-Agent", QqClient.userAgent()).build()
            QqClient.http.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return null
                val bytes = resp.body?.bytes() ?: return null
                if (bytes.size < 512) return null
                dst.writeBytes(bytes)
            }
            dst
        }.getOrNull()
    }

    /** 把歌词拼成标准 LRC 文本，便于内嵌。 */
    fun toLrc(mid: String): String {
        val lines = runCatching { LyricApi.fetch(mid) }.getOrNull() ?: return ""
        if (lines.isEmpty()) return ""
        val sb = StringBuilder()
        for (l in lines) {
            val sec = l.timeMs / 1000
            sb.append('[')
                .append((sec / 60).toString().padStart(2, '0')).append(':')
                .append((sec % 60).toString().padStart(2, '0')).append('.')
                .append((l.timeMs % 1000).toString().padStart(3, '0'))
                .append(']').append(l.text).append('\n')
        }
        return sb.toString()
    }
}
