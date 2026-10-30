package com.tianxiaodudou.qqmusicdownloader.ui

import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
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
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private enum class LoginMode(val label: String) { QQ("QQ 扫码"), WX("微信扫码"), SMS("手机验证码") }

@Composable
fun LoginScreen(onLoggedIn: (Session) -> Unit) {
    var mode by remember { mutableStateOf(LoginMode.QQ) }

    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text("QQ音乐下载器", fontSize = 24.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.onBackground)
        Spacer(Modifier.height(4.dp))
        Text("登录 QQ 音乐账号", color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(16.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            LoginMode.entries.forEach { m ->
                FilterChip(selected = mode == m, onClick = { mode = m }, label = { Text(m.label) })
            }
        }
        Spacer(Modifier.height(18.dp))
        when (mode) {
            LoginMode.QQ -> QrBlock(onLoggedIn, wechat = false)
            LoginMode.WX -> QrBlock(onLoggedIn, wechat = true)
            LoginMode.SMS -> SmsBlock(onLoggedIn)
        }
    }
}

@Composable
private fun QrBlock(onLoggedIn: (Session) -> Unit, wechat: Boolean) {
    var image by remember { mutableStateOf<ImageBitmap?>(null) }
    var status by remember { mutableStateOf("正在获取二维码…") }
    var reload by remember { mutableIntStateOf(0) }

    LaunchedEffect(reload, wechat) {
        image = null
        status = "正在获取二维码…"
        var uuid = ""
        val bytes = withContext(Dispatchers.IO) {
            runCatching {
                if (wechat) {
                    val q = LoginApi.wxQrShow()
                    uuid = q.uuid
                    q.image
                } else {
                    LoginApi.qrShow().image
                }
            }.getOrNull()
        }
        if (bytes == null) {
            status = "二维码获取失败，检查网络后重试"
            return@LaunchedEffect
        }
        image = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap()
        status = if (wechat) "打开微信扫一扫" else "打开手机 QQ 扫一扫"
        while (true) {
            delay(2000)
            val st = withContext(Dispatchers.IO) {
                runCatching { if (wechat) LoginApi.wxPoll(uuid) else LoginApi.poll() }.getOrNull()
            }
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
                is LoginApi.State.Failed -> {
                    status = "登录失败：" + st.message
                    return@LaunchedEffect
                }
                else -> Unit
            }
        }
    }

    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Box(
            modifier = Modifier.size(210.dp).clip(RoundedCornerShape(14.dp)).background(Color.White),
            contentAlignment = Alignment.Center,
        ) {
            val bmp = image
            if (bmp != null) Image(bitmap = bmp, contentDescription = "二维码", modifier = Modifier.size(200.dp))
            else Text("…", color = Color(0xFF666666))
        }
        Spacer(Modifier.height(12.dp))
        Text(status, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(12.dp))
        OutlinedButton(onClick = { reload += 1 }) { Text("重新获取二维码") }
    }
}

@Composable
private fun SmsBlock(onLoggedIn: (Session) -> Unit) {
    var phone by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    var status by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.fillMaxWidth()) {
        OutlinedTextField(
            value = phone,
            onValueChange = { phone = it.filter { c -> c.isDigit() }.take(11) },
            singleLine = true,
            label = { Text("手机号") },
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(10.dp))
        OutlinedButton(
            enabled = !busy && phone.length >= 11,
            onClick = {
                busy = true
                scope.launch {
                    status = withContext(Dispatchers.IO) { runCatching { LoginApi.sendSms(phone) }.getOrElse { "发送失败：" + it.message } }
                    busy = false
                }
            },
        ) { Text("发送验证码") }
        Spacer(Modifier.height(10.dp))
        OutlinedTextField(
            value = code,
            onValueChange = { code = it.filter { c -> c.isDigit() }.take(6) },
            singleLine = true,
            label = { Text("验证码") },
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(14.dp))
        Button(
            enabled = !busy && phone.length >= 11 && code.length >= 4,
            onClick = {
                busy = true
                scope.launch {
                    val r = withContext(Dispatchers.IO) { runCatching { LoginApi.smsLogin(phone, code) } }
                    busy = false
                    r.onSuccess { onLoggedIn(it) }
                        .onFailure { status = "登录失败：" + it.message }
                }
            },
        ) { Text("登录") }
        if (status.isNotEmpty()) {
            Spacer(Modifier.height(10.dp))
            Text(status, color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 12.sp)
        }
    }
}
