package app.accessfromphone

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder

/**
 * Keeps a long download alive while the phone is locked or the app is in the background.
 * The page does the fetching; this only holds the app's process up and shows one ongoing notification with the progress.
 */
class DownloadService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val title = intent?.getStringExtra(TITLE) ?: getString(R.string.app_name)
        val text = intent?.getStringExtra(TEXT) ?: ""
        val percent = intent?.getIntExtra(PERCENT, -1) ?: -1
        val notification = build(this, title, text, percent)
        startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        return START_NOT_STICKY
    }

    companion object {
        const val CHANNEL = "afp_downloads"
        const val NOTIFICATION_ID = 4107
        const val TITLE = "title"
        const val TEXT = "text"
        const val PERCENT = "percent"

        private fun build(context: Context, title: String, text: String, percent: Int): Notification {
            val manager = context.getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(NotificationChannel(CHANNEL, context.getString(R.string.downloads_channel), NotificationManager.IMPORTANCE_LOW))
            val open = PendingIntent.getActivity(context, 0, Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP), PendingIntent.FLAG_IMMUTABLE)
            val builder = Notification.Builder(context, CHANNEL)
                .setSmallIcon(R.drawable.ic_stat_afp).setContentTitle(title).setContentText(text)
                .setOngoing(true).setOnlyAlertOnce(true).setContentIntent(open)
            if (percent in 0..100) builder.setProgress(100, percent, false) else builder.setProgress(0, 0, true)
            return builder.build()
        }

        /** Show or update the notification (starting the service the first time). */
        fun show(context: Context, title: String, text: String, percent: Int) {
            context.startForegroundService(Intent(context, DownloadService::class.java).putExtra(TITLE, title).putExtra(TEXT, text).putExtra(PERCENT, percent))
        }

        fun hide(context: Context) {
            context.stopService(Intent(context, DownloadService::class.java))
        }
    }
}
