package com.tianxiaodudou.qqmusicdownloader.net

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract

/** 下载目录：用系统文件选择器（SAF）授权一棵树，权限持久化，重启后仍可写。 */
object DirStore {

    private const val PREFS = "download_dir"
    private const val KEY = "tree_uri"

    fun get(context: Context): Uri? {
        val raw = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, null) ?: return null
        return Uri.parse(raw)
    }

    fun save(context: Context, uri: Uri) {
        context.contentResolver.takePersistableUriPermission(
            uri,
            Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION,
        )
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY, uri.toString()).apply()
    }

    fun clear(context: Context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().remove(KEY).apply()
    }

    /** 把文件写进选定目录，返回给用户看的路径文案。 */
    fun write(context: Context, tree: Uri, name: String, mime: String, from: java.io.File): String {
        val resolver = context.contentResolver
        val parent = DocumentsContract.buildDocumentUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree))
        val doc = DocumentsContract.createDocument(resolver, parent, mime, name)
            ?: throw IllegalStateException("选定的目录不可写，请重新选择")
        resolver.openOutputStream(doc)?.use { out -> from.inputStream().copyTo(out) }
            ?: throw IllegalStateException("写入失败")
        return displayPath(context, tree) + "/" + name
    }

    private fun displayPath(context: Context, tree: Uri): String {
        val docId = runCatching { DocumentsContract.getTreeDocumentId(tree) }.getOrNull().orEmpty()
        val parts = docId.split(":", limit = 2)
        return if (parts.size == 2 && parts[1].isNotEmpty()) parts[1] else "已选目录"
    }
}
