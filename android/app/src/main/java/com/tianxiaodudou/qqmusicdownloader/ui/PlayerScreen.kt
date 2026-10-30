package com.tianxiaodudou.qqmusicdownloader.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.foundation.clickable
import androidx.compose.material3.AlertDialog
import androidx.compose.runtime.rememberCoroutineScope
import androidx.media3.exoplayer.ExoPlayer
import com.tianxiaodudou.qqmusicdownloader.net.LyricApi
import com.tianxiaodudou.qqmusicdownloader.net.MusicApi
import com.tianxiaodudou.qqmusicdownloader.net.PlaylistApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

@Composable
fun PlayerScreen(song: MusicApi.Song, player: ExoPlayer, onBack: () -> Unit) {
    var lines by remember(song.mid) { mutableStateOf<List<LyricApi.Line>>(emptyList()) }
    var posMs by remember { mutableLongStateOf(0L) }
    var playing by remember { mutableStateOf(player.isPlaying) }
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()
    var msg by remember { mutableStateOf("") }
    var showAdd by remember { mutableStateOf(false) }
    var lists by remember { mutableStateOf<List<PlaylistApi.Item>>(emptyList()) }
    LaunchedEffect(showAdd) {
        if (showAdd && lists.isEmpty()) {
            lists = withContext(Dispatchers.IO) { runCatching { PlaylistApi.mine() }.getOrNull() ?: emptyList() }
        }
    }

    LaunchedEffect(song.mid) {
        lines = withContext(Dispatchers.IO) {
            runCatching { LyricApi.fetch(song.mid) }.getOrNull() ?: emptyList()
        }
    }

    // 60ms 一跳，跟随播放位置；暂停时不动
    LaunchedEffect(song.mid) {
        while (true) {
            posMs = player.currentPosition
            playing = player.isPlaying
            delay(60)
        }
    }

    val current = lines.indexOfLast { it.timeMs <= posMs }
    LaunchedEffect(current) {
        if (current >= 0) listState.animateScrollToItem(current)
    }

    Column(
        Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).padding(horizontal = 20.dp),
    ) {
        Spacer(Modifier.height(12.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = onBack) { Text("收起", color = MaterialTheme.colorScheme.primary) }
            Spacer(Modifier.weight(1f))
            Column(horizontalAlignment = Alignment.End) {
                Text(song.name, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                Text(song.singers, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }

        Box(Modifier.weight(1f)) {
            if (lines.isEmpty()) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text("暂无歌词", color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            } else {
                LazyColumn(state = listState, verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    items(lines.size) { index ->
                        val line = lines[index]
                        val isCurrent = index == current
                        Text(
                            buildAnnotatedString {
                                if (isCurrent && line.words.isNotEmpty()) {
                                    val filled = filledChars(line, posMs)
                                    val total = line.text.length
                                    withStyle(SpanStyle(color = MaterialTheme.colorScheme.primary, fontWeight = FontWeight.SemiBold)) {
                                        append(line.text.take(filled))
                                    }
                                    withStyle(SpanStyle(color = MaterialTheme.colorScheme.onSurfaceVariant)) {
                                        append(line.text.drop(filled.coerceIn(0, total)))
                                    }
                                } else {
                                    append(line.text)
                                }
                            },
                            fontSize = if (isCurrent) 17.sp else 15.sp,
                            color = if (isCurrent) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.55f),
                            textAlign = TextAlign.Center,
                            modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp),
                        )
                        if (isCurrent && line.translation.isNotEmpty()) {
                            Text(
                                line.translation,
                                fontSize = 12.sp,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                textAlign = TextAlign.Center,
                                modifier = Modifier.fillMaxWidth(),
                            )
                        }
                    }
                }
            }
        }

        val duration = player.duration.coerceAtLeast(1L)
        LinearProgressIndicator(
            progress = { (posMs.toFloat() / duration).coerceIn(0f, 1f) },
            modifier = Modifier.fillMaxWidth(),
            color = MaterialTheme.colorScheme.primary,
            trackColor = MaterialTheme.colorScheme.surfaceVariant,
        )
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
            Text(fmt(posMs), fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.weight(1f))
            Text(fmt(duration), fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Row(
            Modifier.fillMaxWidth().padding(vertical = 8.dp),
            horizontalArrangement = Arrangement.Center,
        ) {
            TextButton(onClick = { if (player.isPlaying) player.pause() else player.play() }) {
                Text(if (playing) "暂停" else "播放", fontSize = 18.sp, color = MaterialTheme.colorScheme.primary)
            }
            TextButton(onClick = {
                scope.launch {
                    val ok = withContext(Dispatchers.IO) { runCatching { PlaylistApi.like(song) }.getOrNull() } == true
                    msg = if (ok) "已收藏到「我喜欢」" else "收藏失败"
                }
            }) { Text("收藏", fontSize = 14.sp) }
            TextButton(onClick = { showAdd = true }) { Text("加到歌单", fontSize = 14.sp) }
        }
        if (msg.isNotEmpty()) {
            Text(msg, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(bottom = 6.dp))
        }
        if (showAdd) {
            AlertDialog(
                onDismissRequest = { showAdd = false },
                title = { Text("加到歌单") },
                text = {
                    if (lists.isEmpty()) Text("还没有歌单")
                    else LazyColumn { items(lists.size) { i ->
                        val it = lists[i]
                        Text(
                            it.name + "（" + it.count + " 首）",
                            modifier = Modifier.fillMaxWidth().clickable {
                                showAdd = false
                                scope.launch {
                                    val ok = withContext(Dispatchers.IO) {
                                        runCatching { PlaylistApi.addSong(it.dirId.toIntOrNull() ?: 0, it.id.toIntOrNull() ?: 0, song) }.getOrNull()
                                    } == true
                                    msg = if (ok) "已加到「" + it.name + "」" else "加入失败"
                                }
                            }.padding(vertical = 12.dp),
                        )
                    } }
                },
                confirmButton = { TextButton(onClick = { showAdd = false }) { Text("关闭") } },
            )
        }
    }
}

/** 当前句按字级时间轴算已唱到的字数。 */
private fun filledChars(line: LyricApi.Line, posMs: Long): Int {
    var done = 0
    for (w in line.words) {
        val start = w.startMs.toLong()
        val end = start + w.durMs
        if (posMs >= end) {
            done += w.text.length
        } else if (posMs > start) {
            val frac = (posMs - start).toFloat() / w.durMs.coerceAtLeast(50).toFloat()
            done += (w.text.length * frac).toInt()
            break
        } else break
    }
    return done.coerceIn(0, line.text.length)
}

private fun fmt(ms: Long): String {
    val total = ms / 1000
    return (total / 60).toString().padStart(2, '0') + ":" + (total % 60).toString().padStart(2, '0')
}
