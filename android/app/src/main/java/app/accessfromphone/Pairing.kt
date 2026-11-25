package app.accessfromphone

import java.net.URI

/** What the PC's QR code and a typed address mean. Kept free of Android classes so it can be unit-tested on the PC. */
object Pairing {
    data class Target(val origin: String, val url: String)

    private val PAIR = Regex("^[A-Za-z0-9]{4}-?[A-Za-z0-9]{4}$")

    /**
     * The QR code on the PC holds https://host/#pair=XXXX-XXXX (the part after the # is never sent to a server, so no proxy or relay log
     * can hold the code). An older PC's code, https://host/?pair=XXXX-XXXX, is still accepted. Anything else is not ours.
     */
    fun fromQr(text: String): Target? {
        val uri = parse(text.trim()) ?: return null
        val pair = query(uri.rawFragment, "pair") ?: query(uri.rawQuery, "pair") ?: return null
        if (!PAIR.matches(pair)) return null
        val origin = originOf(uri)
        return Target(origin, "$origin/#pair=$pair")
    }

    /** A hand-typed address: "192.168.1.5:8765", "abc123.xyz.com" or a full link. The page then asks for the code itself. */
    fun fromAddress(text: String): Target? {
        var raw = text.trim().trimEnd('/')
        if (raw.isEmpty() || raw.any { it.isWhitespace() }) return null
        if (!raw.contains("://")) raw = "https://$raw"  // the PC's link is encrypted by default; type http:// in front if you turned that off
        val uri = parse(raw) ?: return null
        val origin = originOf(uri)
        return Target(origin, origin + "/")
    }

    /** What was typed or pasted into the code boxes, made tidy: letters and digits only, capitals, at most 8. */
    fun cleanCode(text: String): String = text.filter { it.code < 128 && it.isLetterOrDigit() }.uppercase().take(8)

    /** The address typed on the first screen plus the code under the PC's QR code: the same as scanning it. */
    fun fromAddressAndCode(address: String, code: String): Target? {
        val clean = cleanCode(code)
        if (clean.length != 8) return null
        val target = fromAddress(address) ?: return null
        return Target(target.origin, "${target.origin}/#pair=${clean.take(4)}-${clean.drop(4)}")
    }

    /** The address of a PC found on the Wi-Fi: its announced host and port, encrypted unless the PC says it turned that off. */
    fun originFor(host: String, port: Int, https: Boolean): String? {
        val h = host.trim().substringBefore('%')  // an IPv6 address may carry a "%wlan0" tail
        if (h.isEmpty() || port !in 1..65535 || h.any { it.isWhitespace() || it == '/' }) return null
        return (if (https) "https" else "http") + "://" + (if (h.contains(':')) "[$h]" else h) + ":" + port
    }

    fun hostOf(origin: String): String? = parse(origin)?.host

    private fun parse(text: String): URI? = try {
        URI(text).takeIf { (it.scheme == "http" || it.scheme == "https") && !it.host.isNullOrEmpty() }
    } catch (e: Exception) {
        null
    }

    private fun originOf(uri: URI): String = uri.scheme + "://" + uri.host + (if (uri.port != -1) ":" + uri.port else "")

    private fun query(rawQuery: String?, name: String): String? =
        rawQuery?.split('&')?.map { it.split('=', limit = 2) }?.firstOrNull { it[0] == name && it.size == 2 }?.get(1)

    /**
     * Addresses on the person's own network. Only for these does the app accept the PC's self-made certificate
     * (and only the one it saw first). A name on the internet must have a certificate from a real authority.
     */
    fun isPrivate(host: String): Boolean {
        val h = host.lowercase()
        if (h == "localhost" || h.endsWith(".localhost") || h.endsWith(".local")) return true
        val p = h.split('.').mapNotNull { it.toIntOrNull() }
        if (p.size != 4 || p.any { it !in 0..255 }) return false
        return p[0] == 10 || p[0] == 127 || (p[0] == 192 && p[1] == 168) || (p[0] == 172 && p[1] in 16..31) || (p[0] == 169 && p[1] == 254)
    }
}
