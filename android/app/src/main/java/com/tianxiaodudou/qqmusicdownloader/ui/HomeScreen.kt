package com.tianxiaodudou.qqmusicdownloader.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.tianxiaodudou.qqmusicdownloader.net.MusicApi
import com.tianxiaodudou.qqmusicdownloader.net.TopApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

@Composable
fun HomeScreen(onPlay: (MusicApi.Song) -> Unit, onDownload: (MusicApi.Song) -> Unit) {
    var charts by remember { mutableStateOf<List<TopApi.Chart>?>(null) }
    var opened by remember { mutableStateOf<TopApi.Chart?>(null) }
    var songs by remember { mutableStateOf<List<MusicApi.Song>?>(null) }
    var error by remember { mutableStateOf("") }

    LaunchedEffect(Unit) {
        val r = withContext(Dispatchers.IO) { runCatching { TopApi.charts() } }
        r.onSuccess { charts = it }.onFailure { error = it.message ?: "榜单加载失败"; charts = emptyList() }
    }
    LaunchedEffect(opened) {
        val c = opened ?: return@LaunchedEffect
        songs = null
        val r = withContext(Dispatchers.IO) { runCatching { TopApi.songs(c) } }
        r.onSuccess { songs = it }.onFailure { error = it.message ?: "歌曲加载失败"; songs = emptyList() }
    }

    val current = opened
    if (current == null) {
        Column(Modifier.fillMaxSize()) {
            Row(Modifier.fillMaxWidth().padding(16.dp)) {
                Text("排行榜", fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
            }
            val list = charts
            if (list == null) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
            } else if (list.isEmpty()) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text(error.ifEmpty { "暂无榜单" }, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            } else {
                LazyColumn(Modifier.fillMaxSize()) {
                    items(list) { c ->
                        Row(
                            Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp))
                                .clickable { opened = c }
                                .padding(horizontal = 16.dp, vertical = 14.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(c.name, maxLines = 1, overflow = TextOverflow.Ellipsis, fontSize = 15.sp)
                        }
                    }
                }
            }
        }
    } else {
        Column(Modifier.fillMaxSize()) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = { opened = null }) { Text("返回") }
                Text(current.name, fontSize = 16.sp, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            val list = songs
            if (list == null) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
            } else {
                LazyColumn(Modifier.fillMaxSize()) {
                    items(list) { song ->
                        Row(
                            Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp))
                                .clickable { onPlay(song) }
                                .padding(horizontal = 16.dp, vertical = 10.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(Modifier.weight(1f)) {
                                Text(song.name, maxLines = 1, overflow = TextOverflow.Ellipsis, fontSize = 15.sp)
                                Text(
                                    song.singers, maxLines = 1, overflow = TextOverflow.Ellipsis,
                                    fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            TextButton(onClick = { onDownload(song) }) { Text("下载", fontSize = 12.sp) }
                        }
                    }
                }
            }
        }
    }
}
