package app.accessfromphone

import android.Manifest
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage

/** Receives what the PC sends through Firebase and shows it. The messages carry text only: this draws the notification. */
class PushService : FirebaseMessagingService() {

    /** Firebase changed this phone's address: tell the page, which hands the new one to the PC. (If the app is closed, it does this on its next start.) */
    override fun onNewToken(token: String) {
        MainActivity.current?.let { activity -> activity.runOnUiThread { activity.sendPushToken(token) } }
    }

    override fun onMessageReceived(message: RemoteMessage) {
        val note = PushLink.fromData(message.data) ?: return
        // "Your PC is going to sleep / shutting down": kept, so that if the PC then goes quiet the popup can say why.
        if (note.kind.startsWith("pc_")) PcStore(this).notice = "${note.kind}|${System.currentTimeMillis()}"
        val open = MainActivity.current
        if (MainActivity.visible && open != null) {  // the app is open in front of the person: the page just updates, no banner
            open.runOnUiThread { open.nudge(note.kind) }
            return
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return
        Push.ensureChannel(this)

        val tap = Intent(this, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            .putExtra(Push.EXTRA_JOB, note.job)
        val pending = PendingIntent.getActivity(this, note.job.hashCode(), tap, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val built = NotificationCompat.Builder(this, Push.CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_afp)
            .setColor(ContextCompat.getColor(this, R.color.accent1))
            .setContentTitle(note.title.ifEmpty { getString(R.string.app_name) })
            .setContentText(note.body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(note.body))
            .setContentIntent(pending)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_MESSAGE)
            .build()
        // The same tag replaces an earlier notification about the same request ("plan ready" becomes "job finished").
        NotificationManagerCompat.from(this).notify(note.job.ifEmpty { note.kind.ifEmpty { "general" } }, 1, built)
    }
}
