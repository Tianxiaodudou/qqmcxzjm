package com.tianxiaodudou.qqmusicdownloader

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.tianxiaodudou.qqmusicdownloader.ui.AppRoot
import com.tianxiaodudou.qqmusicdownloader.ui.theme.QQMusicTheme

/**
 * 单 Activity + Compose。
 *
 * 这一版是独立运行的安卓客户端：登录、接口、解密、下载全部在手机上完成，
 * 不再需要飞牛 NAS，也不再是 WebView 套网页。
 */
class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            QQMusicTheme {
                AppRoot()
            }
        }
    }
}
