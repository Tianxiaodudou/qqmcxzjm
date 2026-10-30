package com.tianxiaodudou.qqmusicdownloader

import android.annotation.SuppressLint
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.webkit.CookieManager
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.enableEdgeToEdge
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat

class MainActivity : ComponentActivity() {

    private lateinit var root: FrameLayout
    private lateinit var webView: WebView
    private lateinit var errorBox: LinearLayout
    private lateinit var errorText: TextView
    private var currentUrl: String? = null

    private val backCallback = object : OnBackPressedCallback(false) {
        override fun handleOnBackPressed() {
            if (webView.canGoBack()) {
                webView.goBack()
            } else {
                // 交给系统做默认的退出（含 Android 13+ 的预测性返回动画）
                isEnabled = false
                onBackPressedDispatcher.onBackPressed()
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        root = FrameLayout(this)
        root.setBackgroundColor(getColor(R.color.brand_bg))

        webView = createWebView()
        root.addView(webView, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))

        errorBox = createErrorBox()
        errorBox.visibility = View.GONE
        root.addView(errorBox, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))

        setContentView(root)

        // 窗口边到边：把系统栏与刘海的高度让给内容（网页自己还会用 env(safe-area-inset-*) 留白，
        // 这里已经扣掉，所以不会出现双重留白）。
        ViewCompat.setOnApplyWindowInsetsListener(root) { view, insets ->
            val bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout()
            )
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            WindowInsetsCompat.CONSUMED
        }
        onBackPressedDispatcher.addCallback(this, backCallback)

        val saved = ServerConfig.getServer(this)
        if (saved == null) {
            showServerDialog(firstRun = true)
        } else {
            loadServer(saved)
        }
        handleIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent)
    }

    override fun onDestroy() {
        if (::webView.isInitialized) {
            root.removeView(webView)
            webView.destroy()
        }
        super.onDestroy()
    }

    // ---------------------------------------------------------------- WebView

    @SuppressLint("SetJavaScriptEnabled")
    private fun createWebView(): WebView {
        val view = WebView(this)
        view.setBackgroundColor(getColor(R.color.brand_bg))
        view.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            // 播放页是「点一下开始播」，但列表里换台/续播会复用同一个 audio，
            // 关掉手势限制，避免自动续播被 WebView 拦掉。
            mediaPlaybackRequiresUserGesture = false
            useWideViewPort = true
            loadWithOverviewMode = true
            setSupportZoom(false)
            builtInZoomControls = false
            displayZoomControls = false
            textZoom = 100
            cacheMode = WebSettings.LOAD_DEFAULT
            mixedContentMode = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
            userAgentString = userAgentString + " QQDroid/" + BuildConfig.VERSION_NAME
        }
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)
        CookieManager.getInstance().apply {
            setAcceptCookie(true)
            setAcceptThirdPartyCookies(view, true)
        }

        view.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(
                view: WebView,
                request: WebResourceRequest,
            ): Boolean {
                val target = request.url
                when (target.scheme?.lowercase()) {
                    null, "http", "https" -> {
                        val baseHost = currentUrl?.let { Uri.parse(it).host }
                        // 同一个 NAS 的页面留在 WebView 里，外站链接交给系统浏览器
                        if (baseHost != null && target.host == baseHost) return false
                        openExternally(target)
                        return true
                    }
                    "about", "blob", "data", "javascript" -> return false
                    else -> {
                        openExternally(target)
                        return true
                    }
                }
            }

            override fun doUpdateVisitedHistory(view: WebView, url: String, isReload: Boolean) {
                backCallback.isEnabled = view.canGoBack()
            }

            override fun onPageFinished(view: WebView, url: String) {
                backCallback.isEnabled = view.canGoBack()
                errorBox.visibility = View.GONE
            }

            override fun onReceivedError(
                view: WebView,
                request: WebResourceRequest,
                error: WebResourceError,
            ) {
                if (request.isForMainFrame) {
                    showError(error.description?.toString().orEmpty())
                }
            }
        }
        view.webChromeClient = WebChromeClient()
        return view
    }

    private fun loadServer(url: String) {
        currentUrl = url
        errorBox.visibility = View.GONE
        webView.loadUrl(url)
    }

    private fun openExternally(uri: Uri) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, uri))
        } catch (e: ActivityNotFoundException) {
            Toast.makeText(this, R.string.no_app, Toast.LENGTH_SHORT).show()
        }
    }

    // ------------------------------------------------------------- 原生兜底界面

    private fun createErrorBox(): LinearLayout {
        val density = resources.displayMetrics.density
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding((28 * density).toInt(), 0, (28 * density).toInt(), 0)
            setBackgroundColor(getColor(R.color.brand_bg))
        }
        box.addView(TextView(this).apply {
            text = getString(R.string.error_title)
            textSize = 18f
            setTextColor(getColor(R.color.text_primary))
        })
        errorText = TextView(this).apply {
            textSize = 13f
            setTextColor(getColor(R.color.text_secondary))
            gravity = Gravity.CENTER
            setPadding(0, (10 * density).toInt(), 0, (22 * density).toInt())
        }
        box.addView(errorText)
        box.addView(Button(this).apply {
            text = getString(R.string.retry)
            setOnClickListener {
                errorBox.visibility = View.GONE
                currentUrl?.let { webView.loadUrl(it) }
            }
        }, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply {
            bottomMargin = (8 * density).toInt()
        })
        box.addView(Button(this).apply {
            text = getString(R.string.change_server)
            setOnClickListener { showServerDialog(firstRun = false) }
        }, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT))
        return box
    }

    private fun showError(detail: String) {
        errorText.text = getString(R.string.error_body, currentUrl ?: "", detail)
    }

    // ---------------------------------------------------------------- 服务器设置

    private fun showServerDialog(firstRun: Boolean) {
        val density = resources.displayMetrics.density
        val pad = (22 * density).toInt()

        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, (8 * density).toInt(), pad, 0)
        }
        val input = EditText(this).apply {
            hint = getString(R.string.dialog_hint)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            isSingleLine = true
            setText(rememberedHost())
        }
        box.addView(input)
        box.addView(TextView(this).apply {
            text = getString(R.string.dialog_tip, ServerConfig.APP_PATH)
            textSize = 12f
            setTextColor(getColor(R.color.text_secondary))
            setPadding(0, (10 * density).toInt(), 0, 0)
        })

        val dialog = AlertDialog.Builder(this)
            .setTitle(if (firstRun) R.string.dialog_title_first else R.string.dialog_title)
            .setView(box)
            .setPositiveButton(R.string.dialog_connect, null)
            .setNegativeButton(if (firstRun) R.string.dialog_quit else R.string.dialog_cancel) { _, _ ->
                if (firstRun) finish()
            }
            .setCancelable(!firstRun)
            .create()

        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                val url = ServerConfig.normalize(input.text.toString())
                if (url == null) {
                    Toast.makeText(this, R.string.invalid_address, Toast.LENGTH_SHORT).show()
                } else {
                    ServerConfig.setServer(this, url)
                    dialog.dismiss()
                    loadServer(url)
                }
            }
        }
        dialog.show()
    }

    /** 对话框里回填上次填过的地址（去掉自动补的路径，用户改起来更短）。 */
    private fun rememberedHost(): String {
        val saved = ServerConfig.getServer(this) ?: return ""
        return saved.removeSuffix(ServerConfig.APP_PATH).removeSuffix("/")
    }

    private fun handleIntent(intent: Intent?) {
        when (intent?.action) {
            ACTION_SERVER -> showServerDialog(firstRun = false)
            ACTION_RELOAD -> webView.reload()
        }
    }

    companion object {
        const val ACTION_SERVER = "com.tianxiaodudou.qqmusicdownloader.SERVER_SETTINGS"
        const val ACTION_RELOAD = "com.tianxiaodudou.qqmusicdownloader.RELOAD"
    }
}
