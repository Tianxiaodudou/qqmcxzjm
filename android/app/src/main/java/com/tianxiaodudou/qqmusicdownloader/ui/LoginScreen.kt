package com.tianxiaodudou.qqmusicdownloader.ui

import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.tianxiaodudou.qqmusicdownloader.net.LoginApi
import com.tianxiaodudou.qqmusicdownloader.net.Session
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

@Composable
fun LoginScreen(onLoggedIn: (Session) -> Unit) {
    var image by remember { mutableStateOf<ImageBitmap?>(null) }
    var status by remember { mutableStateOf("正在获取二维码…") }
    var reload by remember { mutableIntStateOf(0) }

    LaunchedEffect(reload) {
        image = null
        status = "正在获取二维码…"
        val qr = withContext(Dispatchers.IO) { runCatching { LoginApi.qrShow() }.getOrNull() }
        if (qr == null) {
            status = "二维码获取失败，检查网络后重试"
            return@LaunchedEffect
        }
        image = BitmapFactory.decodeByteArray(qr.image, 0, qr.image.size)?.asImageBitmap()
        status = "打开手机 QQ 扫一扫"
        while (true) {
            delay(2000)
            val st = withContext(Dispatchers.IO) { runCatching { LoginApi.poll() }.getOrNull() }
            when (st) {
                null -> Unit
                is LoginApi.State.LoggedIn -> {
                    onLoggedIn(st.session)
                    return@LaunchedEffect
                }
                is LoginApi.State.Scanned -> status = "已扫码，请在手机上确认"
                is LoginApi.State.Expired -> {
                    status = "二维码已过期，正在刷新…"
                    reload += 1
                    return@LaunchedEffect
                }
                is LoginApi.State.Cancelled -> {
                    status = "已取消，点下面重新获取"
                    return@LaunchedEffect
                }
                else -> Unit
            }
        }
    }

    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text("QQ音乐下载器", fontSize = 24.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.onBackground)
        Spacer(Modifier.height(6.dp))
        Text("扫码登录 QQ 音乐账号", color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(22.dp))
        Box(
            modifier = Modifier.size(220.dp).clip(RoundedCornerShape(14.dp)).background(Color.White),
            contentAlignment = Alignment.Center,
        ) {
            val bmp = image
            if (bmp != null) {
                Image(bitmap = bmp, contentDescription = "登录二维码", modifier = Modifier.size(210.dp))
            } else {
                Text("…", color = Color(0xFF666666))
            }
        }
        Spacer(Modifier.height(16.dp))
        Text(status, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(16.dp))
        OutlinedButton(onClick = { reload += 1 }) { Text("重新获取二维码") }
    }
}
