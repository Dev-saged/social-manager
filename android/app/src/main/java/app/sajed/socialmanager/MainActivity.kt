package app.sajed.socialmanager

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.ContentValues
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.MediaStore
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import java.net.HttpURLConnection
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URL

class MainActivity : AppCompatActivity() {
    private lateinit var web: WebView
    private lateinit var status: TextView
    private lateinit var retry: Button
    private var chooser: ValueCallback<Array<Uri>>? = null
    private val home = "http://127.0.0.1:${ServerService.PORT}/"

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = FrameLayout(this)
        web = WebView(this).apply {
            layoutParams = ViewGroup.LayoutParams(-1, -1)
            visibility = android.view.View.INVISIBLE
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.mediaPlaybackRequiresUserGesture = false
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(v: WebView, r: WebResourceRequest): Boolean {
                    val u = r.url
                    if (u.host == "127.0.0.1" && u.port == ServerService.PORT) return false
                    runCatching { startActivity(Intent(Intent.ACTION_VIEW, u)) }
                    return true
                }
            }
            webChromeClient = object : WebChromeClient() {
                override fun onShowFileChooser(w: WebView, cb: ValueCallback<Array<Uri>>, p: FileChooserParams): Boolean {
                    chooser?.onReceiveValue(null)
                    chooser = cb
                    return try {
                        startActivityForResult(p.createIntent(), REQ_FILE); true
                    } catch (e: Exception) { chooser = null; false }
                }
            }
            setDownloadListener { url, _, disposition, mime, _ ->
                saveDownload(url, URLUtil.guessFileName(url, disposition, mime), mime)
            }
        }
        status = TextView(this).apply {
            text = getString(R.string.starting)
            setTextColor(0xFFF2B84B.toInt()); textSize = 18f
            textDirection = android.view.View.TEXT_DIRECTION_RTL
        }
        retry = Button(this).apply {
            text = getString(R.string.retry); visibility = android.view.View.GONE
            setOnClickListener { visibility = android.view.View.GONE; status.text = getString(R.string.starting); boot() }
        }
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = android.view.Gravity.CENTER
            layoutParams = FrameLayout.LayoutParams(-1, -1)
            addView(status); addView(retry)
        }
        root.addView(box); root.addView(web)
        setContentView(root)

        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)

        if (savedInstanceState != null) web.restoreState(savedInstanceState)
        boot()
    }

    private fun boot() {
        ContextCompat.startForegroundService(this, Intent(this, ServerService::class.java))
        Thread {
            val deadline = System.currentTimeMillis() + 90_000
            var up = false
            while (System.currentTimeMillis() < deadline && ServerService.failure == null) {
                up = try {
                    Socket().use { it.connect(InetSocketAddress("127.0.0.1", ServerService.PORT), 500); true }
                } catch (e: Exception) { false }
                if (up) break
                Thread.sleep(400)
            }
            runOnUiThread {
                if (up) {
                    status.visibility = android.view.View.GONE
                    retry.visibility = android.view.View.GONE
                    web.visibility = android.view.View.VISIBLE
                    if (web.url == null) web.loadUrl(home)
                } else {
                    status.text = getString(R.string.start_failed)
                    retry.visibility = android.view.View.VISIBLE
                }
            }
        }.start()
    }

    private fun saveDownload(url: String, name: String, mime: String?) {
        Thread {
            val ok = try {
                val c = URL(url).openConnection() as HttpURLConnection
                CookieManager.getInstance().getCookie(url)?.let { c.setRequestProperty("Cookie", it) }
                c.inputStream.use { input ->
                    if (Build.VERSION.SDK_INT >= 29) {
                        val v = ContentValues().apply {
                            put(MediaStore.Downloads.DISPLAY_NAME, name)
                            put(MediaStore.Downloads.MIME_TYPE, mime ?: "application/octet-stream")
                            put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
                        }
                        val uri = contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v)!!
                        contentResolver.openOutputStream(uri)!!.use { input.copyTo(it) }
                    } else {
                        val dir = getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS)!!
                        java.io.File(dir, name).outputStream().use { input.copyTo(it) }
                    }
                }
                true
            } catch (e: Exception) { false }
            runOnUiThread {
                Toast.makeText(this, if (ok) R.string.saved_to_downloads else R.string.download_failed, Toast.LENGTH_LONG).show()
            }
        }.start()
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode == REQ_FILE) {
            chooser?.onReceiveValue(
                if (resultCode == Activity.RESULT_OK) android.webkit.WebChromeClient.FileChooserParams.parseResult(resultCode, data) else null
            )
            chooser = null
        } else super.onActivityResult(requestCode, resultCode, data)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        web.saveState(outState)
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack() else super.onBackPressed()
    }

    companion object { private const val REQ_FILE = 42 }
}
