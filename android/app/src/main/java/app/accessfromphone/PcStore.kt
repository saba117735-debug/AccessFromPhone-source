package app.accessfromphone

import android.content.Context

/** Which PC this phone belongs to, and the certificate it showed the first time (so a different one is noticed). */
class PcStore(context: Context) {
    private val prefs = context.getSharedPreferences("afp", Context.MODE_PRIVATE)

    var origin: String?
        get() = prefs.getString("origin", null)
        set(value) {
            val changed = value != origin
            prefs.edit().putString("origin", value).apply { if (changed) remove("pin") }.apply()
        }

    /** SHA-256 of the PC's certificate, kept only for addresses on the person's own network. */
    var pin: String?
        get() = prefs.getString("pin", null)
        set(value) = prefs.edit().putString("pin", value).apply()

    /** The PC this phone belonged to before the last "Switch PC" (and its certificate), so a switch that goes wrong can be undone. */
    val previous: String? get() = prefs.getString("prev_origin", null)

    fun beginSwitch() {
        prefs.edit().putString("prev_origin", origin).putString("prev_pin", pin).apply()
    }

    /** Back to the PC before the switch, with the certificate it had shown. False when there is none. */
    fun revert(): Boolean {
        val old = previous ?: return false
        val oldPin = prefs.getString("prev_pin", null)
        prefs.edit().apply {
            putString("origin", old)
            if (oldPin != null) putString("pin", oldPin) else remove("pin")
            remove("prev_origin"); remove("prev_pin")
        }.apply()
        return true
    }

    fun clearPrevious() = prefs.edit().remove("prev_origin").remove("prev_pin").apply()

    /** The newest "your PC is going to sleep / shutting down" notice, as "kind|time in ms", kept so the popup can say why the PC went quiet. */
    var notice: String?
        get() = prefs.getString("notice", null)
        set(value) = prefs.edit().putString("notice", value).apply()

    fun forget() = prefs.edit().clear().apply()
}
