package com.tianxiaodudou.qqmusicdownloader.net

import android.content.ContentValues
import android.content.Context
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.snapshots.SnapshotStateList
import okhttp3.Request
import java.io.File

/**
 * 下载到手机：无损走「整首下载 + QMC2 解密」，其他走 vkey 直链；
 * Android 10+ 写系统「音乐/QQ音乐下载器」，更低版本写应用私有目录（免权限）。
 */
object Downloader {

    data class Task(
        val id: String,
        val title: String,
        val subtitle: String,
        val state: String,
        val progress: Int,
        val path: String = "",
    )

    val tasks: SnapshotStateList<Task> = mutableStateListOf()

    private fun update(id: String, f: (Task) -> Task) {
        val i = tasks.indexOfFirst { it.id == id }
        if (i >= 0) tasks[i] = f(tasks[i])
    }

    fun enqueue(context: Context, song: MusicApi.Song) {
        if (tasks.any { it.id == song.mid && it.state != "失败" && it.state != "完成" }) return
        if (tasks.none { it.id == song.mid }) {
            tasks.add(0, Task(song.mid, song.name, song.singers, "准备中", 0))
        } else {
            update(song.mid) { it.copy(state = "准备中", progress = 0, path = "") }
        }
        val app = context.applicationContext
        Thread {
            try {
                update(song.mid) { it.copy(state = "下载中", progress = 5) }
                var src = runCatching { Lossless.playableFile(app, song) }.getOrNull()
                var ext = "flac"
                var mime = "audio/flac"
                if (src == null) {
                    val url = MusicApi.songUrl(song) ?: throw IllegalStateException("拿不到播放地址")
                    update(song.mid) { it.copy(progress = 20) }
                    val tmp = File(app.cacheDir, song.mid + ".mp3")
                    val req = Request.Builder().url(url).header("User-Agent", QqClient.userAgent()).build()
                    QqClient.http.newCall(req).execute().use { resp ->
                        val body = resp.body ?: throw IllegalStateException("下载失败")
                        tmp.outputStream().use { out -> body.byteStream().copyTo(out) }
                    }
                    src = tmp
                    ext = "mp3"
                    mime = "audio/mpeg"
                }
                update(song.mid) { it.copy(progress = 70, state = "写标签") }
                runCatching { Tagger.tag(app, src!!, song, Tagger.toLrc(song.mid)) }
                update(song.mid) { it.copy(progress = 90, state = "下载中") }
                val name = safeName(song.name) + "_" + safeName(song.singers) + "_" + song.mid + "." + ext
                val saved = export(app, name, mime, src!!)
                update(song.mid) { it.copy(state = "完成", progress = 100, path = saved) }
                History.add(app, song, saved)
            } catch (e: Exception) {
                update(song.mid) { it.copy(state = "失败", progress = 0, path = e.message ?: "未知错误") }
            }
        }.start()
    }

    private fun export(context: Context, name: String, mime: String, src: File): String {
        // 优先用户用系统文件选择器指定的目录
        val tree = DirStore.get(context)
        if (tree != null) return DirStore.write(context, tree, name, mime, src)
        throw IllegalStateException("还没选下载目录：请到「我的 → 下载目录」选一个文件夹")
    }

    @Suppress("unused")
    private fun exportLegacy(context: Context, name: String, mime: String, src: File): String {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val values = ContentValues().apply {
                put(MediaStore.Audio.Media.DISPLAY_NAME, name)
                put(MediaStore.Audio.Media.MIME_TYPE, mime)
                put(MediaStore.Audio.Media.RELATIVE_PATH, Environment.DIRECTORY_MUSIC + "/QQ音乐下载器")
                put(MediaStore.Audio.Media.IS_MUSIC, 1)
                put(MediaStore.Audio.Media.IS_PENDING, 1)
            }
            val resolver = context.contentResolver
            val uri = resolver.insert(MediaStore.Audio.Media.EXTERNAL_CONTENT_URI, values)
                ?: throw IllegalStateException("系统拒绝写入音乐目录")
            resolver.openOutputStream(uri)?.use { out -> src.inputStream().copyTo(out) }
                ?: throw IllegalStateException("写入失败")
            values.clear()
            values.put(MediaStore.Audio.Media.IS_PENDING, 0)
            resolver.update(uri, values, null, null)
            return "音乐/QQ音乐下载器/" + name
        }
        val dir = File(context.getExternalFilesDir(Environment.DIRECTORY_MUSIC), "QQ音乐下载器")
        if (!dir.exists()) dir.mkdirs()
        val dst = File(dir, name)
        src.copyTo(dst, overwrite = true)
        return dst.absolutePath
    }

    private fun safeName(raw: String): String =
        raw.replace(Regex("""[\\/:*?"<>|]"""), "_").trim().ifEmpty { "未知" }
}
