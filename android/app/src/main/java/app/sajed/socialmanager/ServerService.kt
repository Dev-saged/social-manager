package app.sajed.socialmanager

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File

/** يشغّل خادم Flask المحلي داخل العملية ويبقيه حياً ليعمل المجدول في الخلفية. */
class ServerService : Service() {

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForegroundCompat()
        startServerOnce(applicationContext)
        return START_STICKY
    }

    private fun startForegroundCompat() {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= 26) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL, getString(R.string.notif_channel), NotificationManager.IMPORTANCE_LOW)
            )
        }
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val n: Notification = NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_launcher)
            .setContentTitle(getString(R.string.notif_title))
            .setContentText(getString(R.string.notif_text))
            .setContentIntent(open)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(1, n)
        }
    }

    companion object {
        const val PORT = 38417
        private const val CHANNEL = "server"
        private const val TAG = "SocialManager"
        @Volatile private var started = false
        @Volatile var failure: String? = null

        @Synchronized
        fun startServerOnce(ctx: Context) {
            if (started) return
            started = true
            Thread({
                try {
                    val appDir = AssetInstaller.install(ctx)
                    if (!Python.isStarted()) Python.start(AndroidPlatform(ctx))
                    val home = File(ctx.filesDir, "home").apply { mkdirs() }
                    Python.getInstance().getModule("android_entry")
                        .callAttr("run", appDir.absolutePath, home.absolutePath, PORT)
                } catch (e: Throwable) {
                    failure = e.toString()
                    started = false
                    Log.e(TAG, "server failed", e)
                }
            }, "python-server").start()
        }
    }
}
