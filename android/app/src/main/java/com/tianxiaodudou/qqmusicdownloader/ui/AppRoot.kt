package com.tianxiaodudou.qqmusicdownloader.ui

import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import com.tianxiaodudou.qqmusicdownloader.BuildConfig
import com.tianxiaodudou.qqmusicdownloader.net.DirStore
import com.tianxiaodudou.qqmusicdownloader.net.Settings
import com.tianxiaodudou.qqmusicdownloader.net.Downloader
import com.tianxiaodudou.qqmusicdownloader.net.Lossless
import com.tianxiaodudou.qqmusicdownloader.net.MusicApi
import com.tianxiaodudou.qqmusicdownloader.net.Session
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

enum class Tab(val label: String) {
    Home("首页"),
    Search("搜索"),
    Library("歌单"),
    Downloads("下载"),
    Mine("我的"),
}

@Composable
fun AppRoot() {
    val context = LocalContext.current
    var session by remember { mutableStateOf(Session.load(context)) }
    val current = session

    if (current == null) {
        LoginScreen(onLoggedIn = { session = it })
    } else {
        MainTabs(current, onLogout = {
            Session.clear(context)
            session = null
        })
    }
}

@Composable
private fun MainTabs(session: Session, onLogout: () -> Unit) {
    val context = LocalContext.current
    var tab by rememberSaveable { mutableStateOf(Tab.Home) }
    var nowPlaying by remember { mutableStateOf<MusicApi.Song?>(null) }
    var isPlaying by remember { mutableStateOf(false) }
    var loading by remember { mutableStateOf(false) }
    var showPlayer by remember { mutableStateOf(false) }
    var queue by remember { mutableStateOf<List<MusicApi.Song>>(emptyList()) }
    var qIndex by remember { mutableStateOf(-1) }
    val scope = rememberCoroutineScope()

    var dirLabel by remember { mutableStateOf(DirStore.get(context)?.lastPathSegment?.take(40) ?: "") }
    val pickDir = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri != null) {
            DirStore.save(context, uri)
            dirLabel = uri.lastPathSegment?.take(40) ?: "已选择"
        }
    }

    val player = remember { ExoPlayer.Builder(context).build() }
    DisposableEffect(Unit) {
        val listener = object : Player.Listener {
            override fun onIsPlayingChanged(playing: Boolean) {
                isPlaying = playing
            }
        }
        player.addListener(listener)
        onDispose {
            player.removeListener(listener)
            player.release()
        }
    }

    fun playAt(list: List<MusicApi.Song>, index: Int) {
        if (index !in list.indices) return
        queue = list
        qIndex = index
        playSong(list[index])
    }

    fun playNext() {
        if (qIndex >= 0 && qIndex + 1 < queue.size) playAt(queue, qIndex + 1)
    }

    fun playPrev() {
        if (qIndex > 0) playAt(queue, qIndex - 1)
    }

    fun playSong(song: MusicApi.Song) {
        nowPlaying = song
        loading = true
        scope.launch {
            // 优先无损（整首下载 + QMC2 解密），失败再退回 320k/128k 直链
            val file = withContext(Dispatchers.IO) { runCatching { Lossless.playableFile(context, song) }.getOrNull() }
            loading = false
            val uri = if (file != null) {
                android.net.Uri.fromFile(file)
            } else {
                val url = withContext(Dispatchers.IO) { runCatching { MusicApi.songUrl(song) }.getOrNull() }
                if (url == null) {
                    Toast.makeText(context, "这首歌拿不到播放地址（可能需要会员或已下架）", Toast.LENGTH_SHORT).show()
                    return@launch
                }
                android.net.Uri.parse(url)
            }
            player.setMediaItem(MediaItem.fromUri(uri))
            player.prepare()
            player.play()
        }
    }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        bottomBar = {
            Column {
                val song = nowPlaying
                if (song != null) {
                    MiniBar(
                        title = song.name,
                        subtitle = if (loading) "正在准备无损…" else song.singers,
                        playing = isPlaying,
                        onToggle = { if (isPlaying) player.pause() else player.play() },
                        onOpen = { showPlayer = true },
                    )
                }
                NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
                    Tab.entries.forEach { item ->
                        NavigationBarItem(
                            selected = tab == item,
                            onClick = { tab = item },
                            icon = { Text(item.label.take(1)) },
                            label = { Text(item.label) },
                        )
                    }
                }
            }
        },
    ) { inner ->
        Box(modifier = Modifier.fillMaxSize().padding(inner)) {
            when (tab) {
                Tab.Home -> HomeScreen(onPlay = { list, i -> playAt(list, i) }, onDownload = { Downloader.enqueue(context, it) })
                Tab.Search -> SearchScreen(onPlay = { list, i -> playAt(list, i) }, onDownload = { Downloader.enqueue(context, it) })
                Tab.Library -> LibraryScreen(onPlay = { list, i -> playAt(list, i) }, onDownload = { Downloader.enqueue(context, it) })
                Tab.Downloads -> DownloadsScreen()
                Tab.Mine -> MinePage(session, onLogout, dirLabel, onPickDir = { pickDir.launch(null) })
                else -> Placeholder(tab.label)
            }
            val playing = nowPlaying
            if (showPlayer && playing != null) {
                PlayerScreen(playing, player, onBack = { showPlayer = false }, onPrev = { playPrev() }, onNext = { playNext() })
            }
        }
    }
}

@Composable
private fun MiniBar(title: String, subtitle: String, playing: Boolean, onToggle: () -> Unit, onOpen: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceVariant)
            .clickable { onOpen() }
            .padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis, fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurface)
            Text(
                subtitle,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Box(Modifier.size(34.dp).clickable { onToggle() }, contentAlignment = Alignment.Center) {
            Text(if (playing) "⏸" else "▶", fontSize = 16.sp, color = MaterialTheme.colorScheme.primary)
        }
    }
}

@Composable
private fun Placeholder(label: String) {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Text(label + " · 开发中", color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun MinePage(session: Session, onLogout: () -> Unit, dirLabel: String, onPickDir: () -> Unit) {
    val context = LocalContext.current
    var quality by remember { mutableStateOf(Settings.quality(context)) }
    var toast by remember { mutableStateOf("") }

    Column(modifier = Modifier.fillMaxSize().padding(20.dp)) {
        Text(
            text = session.nickname.ifEmpty { "已登录" },
            fontSize = 20.sp,
            fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.onBackground,
        )
        Text("QQ 音乐 uin " + session.uin, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)

        Spacer(Modifier.height(22.dp))
        Text("播放音质", fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
        Settings.Quality.entries.forEach { q ->
            Row(
                Modifier.fillMaxWidth().clickable {
                    quality = q
                    Settings.setQuality(context, q)
                }.padding(vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(if (quality == q) "● " else "○ ", color = MaterialTheme.colorScheme.primary)
                Text(q.label, fontSize = 13.sp)
            }
        }

        Spacer(Modifier.height(18.dp))
        Text("下载", fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
        Text(
            if (dirLabel.isEmpty()) "目录：未选择" else "目录：" + dirLabel,
            fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(vertical = 6.dp),
        )
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedButton(onClick = onPickDir) { Text("选择下载目录") }
            Spacer(Modifier.width(10.dp))
            OutlinedButton(onClick = {
                val freed = Settings.clearCache(context)
                toast = "已清理 " + (freed / 1024 / 1024) + " MB 缓存"
            }) { Text("清理缓存") }
        }

        if (toast.isNotEmpty()) {
            Spacer(Modifier.height(10.dp))
            Text(toast, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }

        Spacer(Modifier.weight(1f))
        Text("QQ音乐下载器 " + BuildConfig.VERSION_NAME, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(10.dp))
        OutlinedButton(onClick = onLogout) { Text("退出登录") }
    }
}
