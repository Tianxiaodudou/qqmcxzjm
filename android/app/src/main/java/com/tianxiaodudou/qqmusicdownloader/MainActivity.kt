package com.tianxiaodudou.qqmusicdownloader

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.tianxiaodudou.qqmusicdownloader.net.QqClient
import com.tianxiaodudou.qqmusicdownloader.net.Session
import com.tianxiaodudou.qqmusicdownloader.ui.AppRoot
import com.tianxiaodudou.qqmusicdownloader.ui.theme.QQMusicTheme

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        QqClient.init(applicationContext)
        QqClient.session = Session.load(this)
        enableEdgeToEdge()
        setContent {
            QQMusicTheme {
                AppRoot()
            }
        }
    }
}
