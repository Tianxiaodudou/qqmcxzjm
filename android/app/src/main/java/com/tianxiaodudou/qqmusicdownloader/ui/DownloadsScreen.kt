package com.tianxiaodudou.qqmusicdownloader.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.material3.TextButton
import androidx.compose.foundation.layout.Spacer
import com.tianxiaodudou.qqmusicdownloader.net.Downloader
import com.tianxiaodudou.qqmusicdownloader.net.History

@Composable
fun DownloadsScreen() {
    val context = androidx.compose.ui.platform.LocalContext.current
    val refresh = androidx.compose.runtime.remember { androidx.compose.runtime.mutableIntStateOf(0) }
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(16.dp)) {
            Text("下载任务", fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
        }
        val history = androidx.compose.runtime.remember(refresh.intValue) { History.list(context) }
        if (Downloader.tasks.isEmpty() && history.isEmpty()) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text("还没有下载任务", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            return@Column
        }
        LazyColumn(Modifier.fillMaxSize()) {
            items(Downloader.tasks) { task ->
                Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp)) {
                    Row {
                        Column(Modifier.weight(1f)) {
                            Text(task.title, maxLines = 1, overflow = TextOverflow.Ellipsis, fontSize = 15.sp)
                            Text(
                                task.subtitle,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                                fontSize = 11.sp,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        Text(task.state, fontSize = 11.sp, color = MaterialTheme.colorScheme.primary)
                    }
                    if (task.state == "下载中" || task.state == "准备中") {
                        LinearProgressIndicator(
                            progress = { task.progress / 100f },
                            modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
                        )
                    }
                    if (task.state == "完成" && task.path.isNotEmpty()) {
                        Text(task.path, fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    if (task.state == "失败") {
                        Text(task.path, fontSize = 10.sp, color = MaterialTheme.colorScheme.error)
                    }
                }
            }
            if (history.isNotEmpty()) {
                item {
                    Row(Modifier.fillMaxWidth().padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text("下载历史", fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                        Spacer(Modifier.weight(1f))
                        TextButton(onClick = { History.clear(context); refresh.intValue += 1 }) { Text("清空") }
                    }
                }
                items(history) { h ->
                    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp)) {
                        Text(h.title, maxLines = 1, overflow = TextOverflow.Ellipsis, fontSize = 14.sp)
                        Text(
                            h.artist,
                            maxLines = 1, overflow = TextOverflow.Ellipsis,
                            fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        }
    }
}
