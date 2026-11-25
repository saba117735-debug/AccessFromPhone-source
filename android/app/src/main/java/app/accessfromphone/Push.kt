package app.accessfromphone

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.os.Build
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging

/**
 * Notifications from the PC ("Plan ready – tap to approve", "Job finished") through Firebase.
 * They work only in a build made with the project's google-services.json (see "Push notifications" in the README);
 * without it the app still runs, and says so when someone turns notifications on.
 */
object Push {
    const val CHANNEL = "afp_requests"
    const val EXTRA_JOB = "job"

    fun available(context: Context): Boolean = FirebaseApp.getApps(context).isNotEmpty()

    fun ensureChannel(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val channel = NotificationChannel(CHANNEL, context.getString(R.string.push_channel), NotificationManager.IMPORTANCE_HIGH)
        channel.description = context.getString(R.string.push_channel_hint)
        context.getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    /** This phone's address for the PC to notify: (token, null), or (null, why not). */
    fun token(context: Context, done: (String?, String?) -> Unit) {
        if (!available(context)) return done(null, context.getString(R.string.push_unavailable))
        FirebaseMessaging.getInstance().token.addOnCompleteListener { task ->
            val token = if (task.isSuccessful) task.result else null
            if (token.isNullOrBlank()) done(null, context.getString(R.string.push_failed)) else done(token, null)
        }
    }

    /** Notifications off: the old token stops working, so the PC can't reach this phone any more. */
    fun forget(context: Context) {
        if (available(context)) FirebaseMessaging.getInstance().deleteToken()
    }
}
