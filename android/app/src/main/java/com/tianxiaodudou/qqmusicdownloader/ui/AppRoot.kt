package com.tianxiaodudou.qqmusicdownloader.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier

enum class Tab(val label: String) {
    Home("首页"),
    Search("搜索"),
    Library("歌单"),
    Downloads("下载"),
    Mine("我的"),
}

@Composable
fun AppRoot() {
    var tab by rememberSaveable { mutableStateOf(Tab.Home) }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        bottomBar = {
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
        },
    ) { inner ->
        Box(
            modifier = Modifier.fillMaxSize().padding(inner),
            contentAlignment = Alignment.Center,
        ) {
            Text(
                text = tab.label + " · 开发中",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}
