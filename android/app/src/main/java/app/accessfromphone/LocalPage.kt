package app.accessfromphone

/**
 * The page's own files, built into the app for the one screen that has no PC to load from: "Connect to your PC".
 * Plain Kotlin so the mapping is unit-tested. Only these five files are ever served; anything else is "not found".
 */
object LocalPage {
    const val HOST = "appassets.androidplatform.net"
    const val ORIGIN = "https://$HOST"
    const val URL = "$ORIGIN/"

    private val FILES = mapOf(
        "" to "index.html",
        "index.html" to "index.html",
        "static/app.css" to "app.css",
        "static/glass.css" to "glass.css",
        "static/conn.js" to "conn.js",
        "static/offline.js" to "offline.js",
        "static/queue.js" to "queue.js",
        "static/titles.js" to "titles.js",
        "static/access.js" to "access.js",
        "static/screen.js" to "screen.js",
        "static/ux.js" to "ux.js",
        "static/app.js" to "app.js",
        "static/icon.svg" to "icon.svg",
    )

    /** The file in the app's assets for a requested path, or null. */
    fun assetFor(path: String): String? = FILES[path.trim('/')]

    fun mimeOf(name: String): String = when {
        name.endsWith(".html") -> "text/html"
        name.endsWith(".css") -> "text/css"
        name.endsWith(".js") -> "text/javascript"
        name.endsWith(".svg") -> "image/svg+xml"
        else -> "application/octet-stream"
    }

    fun isLocal(url: String?): Boolean = url != null && (url == ORIGIN || url.startsWith("$ORIGIN/"))
}
