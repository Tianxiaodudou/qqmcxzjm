package com.tianxiaodudou.qqmusicdownloader.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
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
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil3.compose.AsyncImage
import com.tianxiaodudou.qqmusicdownloader.net.AlbumApi
import com.tianxiaodudou.qqmusicdownloader.net.MusicApi
import com.tianxiaodudou.qqmusicdownloader.net.PlaylistApi
import com.tianxiaodudou.qqmusicdownloader.net.RecommendApi
import com.tianxiaodudou.qqmusicdownloader.net.SingerApi
import com.tianxiaodudou.qqmusicdownloader.net.TopApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

private sealed interface HomeView {
    data object Feed : HomeView
    data object Guess : HomeView
    data object Radar : HomeView
    data class Chart(val chart: TopApi.Chart) : HomeView
    data class Songlist(val item: RecommendApi.Songlist) : HomeView
    data class Card(val card: RecommendApi.FeedCard) : HomeView
    data object Albums : HomeView
    data object Singers : HomeView
    data class SingerSongs(val singer: SingerApi.Singer) : HomeView
    data class Hot(val kw: String = "") : HomeView
    data class AlbumSongs(val album: AlbumApi.Album) : HomeView
}

@Composable
fun HomeScreen(onPlay: (List<MusicApi.Song>, Int) -> Unit, onDownload: (MusicApi.Song) -> Unit) {
    var charts by remember { mutableStateOf<List<TopApi.Chart>?>(null) }
    var songlists by remember { mutableStateOf<List<RecommendApi.Songlist>?>(null) }
    var newSongs by remember { mutableStateOf<List<MusicApi.Song>?>(null) }
    var feed by remember { mutableStateOf<List<RecommendApi.FeedGroup>?>(null) }
    var albums by remember { mutableStateOf<List<AlbumApi.Album>?>(null) }
    var hot by remember { mutableStateOf<List<String>?>(null) }
    var singers by remember { mutableStateOf<List<SingerApi.Singer>?>(null) }
    var view by remember { mutableStateOf<HomeView>(HomeView.Feed) }
    var songs by remember { mutableStateOf<List<MusicApi.Song>?>(null) }
    var error by remember { mutableStateOf("") }

    LaunchedEffect(Unit) {
        withContext(Dispatchers.IO) {
            runCatching { TopApi.charts() }.onSuccess { charts = it }.onFailure { charts = emptyList() }
            runCatching { RecommendApi.songlists() }.onSuccess { songlists = it }.onFailure { songlists = emptyList() }
            runCatching { RecommendApi.newSongs() }.onSuccess { newSongs = it }.onFailure { newSongs = emptyList() }
            runCatching { RecommendApi.homeFeed() }.onSuccess { feed = it }.onFailure { feed = emptyList() }
        }
        if (charts.isNullOrEmpty() && songlists.isNullOrEmpty() && newSongs.isNullOrEmpty()) {
            error = "推荐加载失败，请检查登录状态"
        }
    }
    LaunchedEffect(view) {
        val v = view
        if (v is HomeView.Feed) return@LaunchedEffect
        if (v is HomeView.Hot && v.kw.isEmpty()) {
            hot = null
            val r = withContext(Dispatchers.IO) { runCatching { MusicApi.hotKeys() } }
            r.onSuccess { hot = it }.onFailure { hot = emptyList() }
            return@LaunchedEffect
        }
        if (v is HomeView.Singers) {
            singers = null
            val r = withContext(Dispatchers.IO) { runCatching { SingerApi.list() } }
            r.onSuccess { singers = it }.onFailure { singers = emptyList() }
            return@LaunchedEffect
        }
        if (v is HomeView.Albums) {
            albums = null
            val r = withContext(Dispatchers.IO) { runCatching { AlbumApi.newAlbums() } }
            r.onSuccess { albums = it }.onFailure { albums = emptyList() }
            return@LaunchedEffect
        }
        songs = null
        error = ""
        val r = withContext(Dispatchers.IO) {
            runCatching {
                when (v) {
                    is HomeView.Guess -> RecommendApi.guess()
                    is HomeView.Radar -> RecommendApi.radar()
                    is HomeView.Chart -> TopApi.songs(v.chart)
                    is HomeView.Songlist -> PlaylistApi.songs(
                        PlaylistApi.Item(v.item.id, v.item.dirId, v.item.name, v.item.songNum)
                    )
                    is HomeView.AlbumSongs -> AlbumApi.songs(v.album.mid)
                    is HomeView.Hot -> MusicApi.search(v.kw)
                    is HomeView.SingerSongs -> SingerApi.songs(v.singer.mid)
                    is HomeView.Card -> {
                        if (v.card.kind == "song") {
                            val sid = v.card.id.toLongOrNull()
                            if (sid == null) emptyList() else listOfNotNull(MusicApi.detail(sid))
                        } else {
                            PlaylistApi.songs(PlaylistApi.Item(v.card.id, "0", v.card.name, 0))
                        }
                    }
                    else -> emptyList()
                }
            }
        }
        r.onSuccess { songs = it }.onFailure { error = it.message ?: "歌曲加载失败"; songs = emptyList() }
    }

    val v = view
    if (v is HomeView.Feed) {
        LazyColumn(Modifier.fillMaxSize()) {
            item { SectionTitle("推荐歌单") }
            val sl = songlists
            if (sl == null) {
                item { LoadingRow() }
            } else if (sl.isEmpty()) {
                item { HintRow("暂无推荐歌单") }
            } else {
                items(sl) { s ->
                    Row(
                        Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp))
                            .clickable { view = HomeView.Songlist(s) }
                            .padding(horizontal = 16.dp, vertical = 10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Cover(s.pic, 48.dp)
                        Column(Modifier.weight(1f).padding(start = 10.dp)) {
                            Text(s.name, maxLines = 1, overflow = TextOverflow.Ellipsis, fontSize = 15.sp)
                            Text(
                                s.songNum.toString() + " 首 · " + fmtPlay(s.listenNum) +
                                    (if (s.creator.isEmpty()) "" else " · " + s.creator),
                                maxLines = 1, overflow = TextOverflow.Ellipsis,
                                fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }

            item { SectionTitle("新歌速递") }
            val ns = newSongs
            if (ns == null) {
                item { LoadingRow() }
            } else if (ns.isEmpty()) {
                item { HintRow("暂无新歌") }
            } else {
                itemsIndexed(ns) { i, song ->
                    SongRow(song, onClick = { onPlay(ns, i) }, onDl = { onDownload(song) })
                }
            }

            item { SectionTitle("为你推荐") }
            val fd = feed
            if (fd == null) {
                item { LoadingRow() }
            } else {
                for (g in fd) {
                    item { SubTitle(g.title) }
                    items(g.cards) { c ->
                        Row(
                            Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp))
                                .clickable { view = HomeView.Card(c) }
                                .padding(horizontal = 16.dp, vertical = 10.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Cover(c.cover, 44.dp)
                            Column(Modifier.weight(1f).padding(start = 10.dp)) {
                                Text(c.name, maxLines = 1, overflow = TextOverflow.Ellipsis, fontSize = 15.sp)
                                Text(
                                    c.sub, maxLines = 1, overflow = TextOverflow.Ellipsis,
                                    fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                    }
                }
            }

            item { SectionTitle("每日推荐") }
            item {
                Row(
                    Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp))
                        .clickable { view = HomeView.Radar }
                        .padding(horizontal = 16.dp, vertical = 12.dp),
                ) { Text("私人雷达（每日 30 首）", fontSize = 15.sp) }
            }

            item { SectionTitle("新碟上架") }
            item {
                Row(
                    Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp))
                        .clickable { view = HomeView.Albums }
                        .padding(horizontal = 16.dp, vertical = 12.dp),
                ) { Text("最新专辑（内地）", fontSize = 15.sp) }
            }

            item { SectionTitle("热门歌手") }
            item {
                Row(
                    Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp))
                        .clickable { view = HomeView.Singers }
                        .padding(horizontal = 16.dp, vertical = 12.dp),
                ) { Text("全部歌手", fontSize = 15.sp) }
            }

            item { SectionTitle("热搜榜") }
            item {
                Row(
                    Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp))
                        .clickable { view = HomeView.Hot() }
                        .padding(horizontal = 16.dp, vertical = 12.dp),
                ) { Text("大家都在搜", fontSize = 15.sp) }
            }

            item { SectionTitle("排行榜") }
            item {
                Row(
                    Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp))
                        .clickable { view = HomeView.Guess }
                        .padding(horizontal = 16.dp, vertical = 12.dp),
                ) { Text("猜你喜欢", fontSize = 15.sp) }
            }
            val cs = charts
            if (cs == null) {
                item { LoadingRow() }
            } else if (cs.isEmpty()) {
                item { HintRow(error.ifEmpty { "暂无榜单" }) }
            } else {
                items(cs) { c ->
                    Row(
                        Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp))
                            .clickable { view = HomeView.Chart(c) }
                            .padding(horizontal = 16.dp, vertical = 12.dp),
                    ) { Text(c.name, maxLines = 1, overflow = TextOverflow.Ellipsis, fontSize = 15.sp) }
                }
            }
            item { Box(Modifier.padding(16.dp)) {} }
        }
    } else if (v is HomeView.Albums) {
        Column(Modifier.fillMaxSize()) {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                TextButton(onClick = { view = HomeView.Feed; albums = null }) { Text("返回") }
                Text("新碟上架", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
            }
            val al = albums
            if (al == null) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
            } else if (al.isEmpty()) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text("暂无新碟", color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            } else {
                LazyColumn(Modifier.fillMaxSize()) {
                    items(al) { a ->
                        Row(
                            Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp))
                                .clickable { view = HomeView.AlbumSongs(a) }
                                .padding(horizontal = 16.dp, vertical = 10.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Cover(a.cover, 48.dp)
                            Column(Modifier.weight(1f).padding(start = 10.dp)) {
                                Text(a.name, maxLines = 1, overflow = TextOverflow.Ellipsis, fontSize = 15.sp)
                                Text(
                                    a.singer, maxLines = 1, overflow = TextOverflow.Ellipsis,
                                    fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                    }
                }
            }
        }
    } else if (v is HomeView.Singers) {
        Column(Modifier.fillMaxSize()) {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                TextButton(onClick = { view = HomeView.Feed; singers = null }) { Text("返回") }
                Text("热门歌手", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
            }
            val sl = singers
            if (sl == null) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
            } else if (sl.isEmpty()) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text("暂无歌手", color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            } else {
                LazyColumn(Modifier.fillMaxSize()) {
                    items(sl) { s ->
                        Row(
                            Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp))
                                .clickable { view = HomeView.SingerSongs(s) }
                                .padding(horizontal = 16.dp, vertical = 12.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(s.name, maxLines = 1, overflow = TextOverflow.Ellipsis, fontSize = 15.sp)
                        }
                    }
                }
            }
        }
    } else if (v is HomeView.Hot && v.kw.isEmpty()) {
        Column(Modifier.fillMaxSize()) {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                TextButton(onClick = { view = HomeView.Feed; hot = null }) { Text("返回") }
                Text("热搜榜", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
            }
            val hk = hot
            if (hk == null) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
            } else if (hk.isEmpty()) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text("暂无热搜", color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            } else {
                LazyColumn(Modifier.fillMaxSize()) {
                    itemsIndexed(hk) { i, kw ->
                        Row(
                            Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp))
                                .clickable { view = HomeView.Hot(kw) }
                                .padding(horizontal = 16.dp, vertical = 12.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text((i + 1).toString(), fontSize = 13.sp, color = MaterialTheme.colorScheme.primary)
                            Text(
                                kw, modifier = Modifier.padding(start = 12.dp), fontSize = 15.sp,
                                maxLines = 1, overflow = TextOverflow.Ellipsis,
                            )
                        }
                    }
                }
            }
        }
    } else {
        val title = when (v) {
            is HomeView.Guess -> "猜你喜欢"
            is HomeView.Radar -> "每日推荐"
            is HomeView.Chart -> v.chart.name
            is HomeView.Songlist -> v.item.name
            is HomeView.Card -> v.card.name
            is HomeView.AlbumSongs -> v.album.name
            is HomeView.Hot -> if (v.kw.isEmpty()) "热搜榜" else v.kw
            is HomeView.SingerSongs -> v.singer.name
            else -> ""
        }
        Column(Modifier.fillMaxSize()) {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                TextButton(onClick = { view = HomeView.Feed; songs = null }) { Text("返回") }
                Text(
                    title, fontSize = 16.sp, fontWeight = FontWeight.SemiBold,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
            }
            val list = songs
            if (list == null) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
            } else if (list.isEmpty()) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text(error.ifEmpty { "暂无歌曲" }, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            } else {
                LazyColumn(Modifier.fillMaxSize()) {
                    itemsIndexed(list) { i, song ->
                        SongRow(song, onClick = { onPlay(list, i) }, onDl = { onDownload(song) })
                    }
                }
            }
        }
    }
}

@Composable
private fun SectionTitle(text: String) {
    Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, top = 18.dp, bottom = 6.dp)) {
        Text(text, fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
private fun SubTitle(text: String) {
    Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, top = 12.dp, bottom = 4.dp)) {
        Text(text, fontSize = 14.sp, color = MaterialTheme.colorScheme.primary)
    }
}

@Composable
private fun LoadingRow() {
    Box(Modifier.fillMaxWidth().padding(16.dp), contentAlignment = Alignment.Center) {
        CircularProgressIndicator(Modifier.padding(4.dp))
    }
}

@Composable
private fun HintRow(text: String) {
    Box(Modifier.fillMaxWidth().padding(16.dp), contentAlignment = Alignment.Center) {
        Text(text, color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp)
    }
}

@Composable
private fun SongRow(song: MusicApi.Song, onClick: () -> Unit, onDl: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp))
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Cover(albumCover(song.albumMid), 44.dp)
        Column(Modifier.weight(1f).padding(start = 10.dp)) {
            Text(song.name, maxLines = 1, overflow = TextOverflow.Ellipsis, fontSize = 15.sp)
            Text(
                song.singers, maxLines = 1, overflow = TextOverflow.Ellipsis,
                fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        TextButton(onClick = onDl) { Text("下载", fontSize = 12.sp) }
    }
}

@Composable
private fun Cover(url: String, dim: Dp) {
    if (url.isEmpty()) return
    AsyncImage(
        model = url,
        contentDescription = null,
        modifier = Modifier.size(dim).clip(RoundedCornerShape(6.dp)),
        contentScale = ContentScale.Crop,
    )
}

private fun albumCover(mid: String): String =
    if (mid.isEmpty()) "" else "https://y.gtimg.cn/music/photo_new/T002R300x300M000" + mid + ".jpg"

private fun fmtPlay(n: Int): String = when {
    n >= 100000000 -> String.format("%.1f 亿", n / 100000000.0)
    n >= 10000 -> String.format("%.1f 万", n / 10000.0)
    else -> n.toString()
}
