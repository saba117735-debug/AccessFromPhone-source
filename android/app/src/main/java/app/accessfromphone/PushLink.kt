package app.accessfromphone

/**
 * What a push notification carries, checked before anything trusts it, and the small scripts the app hands to the page.
 * Plain Kotlin (no Android classes) so it is covered by unit tests.
 */
object PushLink {
    private val ID = Regex("^[A-Za-z0-9_-]{1,40}$")

    /** A notification as the PC sent it. Firebase carries only text: the app draws the notification itself. */
    data class Note(val title: String, val body: String, val job: String, val kind: String)

    /** The request a notification is about, or null when it isn't a plain id (so nothing odd can ever reach the page's script). */
    fun jobId(raw: String?): String? = raw?.takeIf { ID.matches(it) }

    fun fromData(data: Map<String, String>): Note? {
        val title = data["title"]?.trim().orEmpty()
        val body = data["body"]?.trim().orEmpty()
        if (title.isEmpty() && body.isEmpty()) return null
        return Note(title.take(120), body.take(240), jobId(data["job"]).orEmpty(), data["kind"].orEmpty().take(20))
    }

    /** Makes the page open the request a tapped notification was about. */
    fun openScript(raw: String?): String? = jobId(raw)?.let { "location.hash='#job/$it'" }

    /** Tells the page this phone's Firebase token (or why there isn't one). The page then hands it to the PC. */
    fun tokenScript(token: String?, error: String?): String {
        val detail = if (token != null) "{token:${jsString(token)}}" else "{error:${jsString(error ?: "")}}"
        return "window.dispatchEvent(new CustomEvent('afp-push',{detail:$detail}))"
    }

    private val KIND = Regex("^[A-Z_]{2,24}$")
    private val NOTICE = Regex("^[a-z_]{0,24}$")

    /** What the app found out about the connection, for the page's popup: the kind of problem (a fixed word) and whether battery saver is on. */
    fun diagnosisScript(kind: String, powerSave: Boolean): String {
        val safe = if (KIND.matches(kind)) kind else "PC_UNREACHABLE"
        return "window.dispatchEvent(new CustomEvent('afp-diagnosis',{detail:{kind:\"$safe\",powerSave:$powerSave}}))"
    }

    /** A notification arrived while the app is open. `kind` says what it was about ("pc_sleep"...), so the page can remember it. */
    fun nudgeScript(kind: String): String {
        val safe = if (NOTICE.matches(kind)) kind else ""
        return "window.dispatchEvent(new CustomEvent('afp-nudge',{detail:{kind:\"$safe\"}}))"
    }

    /** A text as a JavaScript string literal that cannot end the string, a script tag or a line early. */
    fun jsString(text: String): String = buildString {
        append('"')
        for (c in text) when {
            c == '\\' -> append("\\\\")
            c == '"' -> append("\\\"")
            c == '\n' -> append("\\n")
            c == '\r' -> append("\\r")
            c == '<' || c == '>' || c == '&' || c == ' ' || c == ' ' || c < ' ' -> append("\\u%04x".format(c.code))
            else -> append(c)
        }
        append('"')
    }
}
