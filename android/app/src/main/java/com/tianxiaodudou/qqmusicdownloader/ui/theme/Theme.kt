package com.tianxiaodudou.qqmusicdownloader.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

val Brand = Color(0xFF1FC365)
val BrandDim = Color(0xFF178A49)
val Bg = Color(0xFF0F1115)
val Surface1 = Color(0xFF171A21)
val Surface2 = Color(0xFF1F2430)
val TextMain = Color(0xFFF2F4F8)
val TextSub = Color(0xFF9AA3B2)

private val DarkScheme = darkColorScheme(
    primary = Brand,
    onPrimary = Color(0xFF04150C),
    secondary = BrandDim,
    onSecondary = Color(0xFFF2F4F8),
    background = Bg,
    onBackground = TextMain,
    surface = Surface1,
    onSurface = TextMain,
    surfaceVariant = Surface2,
    onSurfaceVariant = TextSub,
    outline = Color(0xFF2A3040),
)

@Composable
fun QQMusicTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = DarkScheme, content = content)
}
