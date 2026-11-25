package app.accessfromphone

import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest

/**
 * The page's copy of recent chats, kept in the app's own private storage, one file for each PC.
 *
 * It lives here and not in the web view's storage because the web view keeps a separate store for each address: the copy the PC's
 * page made could not be read by the app's built-in page, which is the only page that can open when the PC is off.
 * Plain Kotlin on a folder, so it is unit-tested. Nothing here leaves the phone.
 */
class SavedChats(private val dir: File, private val maxBytes: Int = 4_000_000) {

    /** Saves `json` for this PC. False (and nothing changed) if it is empty or over the size limit. */
    fun write(key: String, json: String): Boolean {
        val bytes = json.toByteArray(Charsets.UTF_8)
        if (key.isBlank() || bytes.isEmpty() || bytes.size > maxBytes) return false
        return runCatching {
            dir.mkdirs()
            val target = fileFor(key)
            val temp = File(dir, target.name + ".tmp")
            temp.writeBytes(bytes)  // written whole first, then swapped in: a crash half-way never leaves a broken copy
            Files.move(temp.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING)
            true
        }.getOrDefault(false)
    }

    /** The saved copy for this PC, or "" when there is none. */
    fun read(key: String): String = runCatching { fileFor(key).takeIf { it.isFile }?.readText(Charsets.UTF_8) ?: "" }.getOrDefault("")

    fun has(key: String?): Boolean = key != null && key.isNotBlank() && fileFor(key).isFile

    fun clear(key: String?) {
        if (key.isNullOrBlank()) return
        runCatching { fileFor(key).delete() }
    }

    fun clearAll() {
        runCatching { dir.listFiles()?.forEach { it.delete() } }
    }

    private fun fileFor(key: String): File {
        val hash = MessageDigest.getInstance("SHA-256").digest(key.trim().lowercase().toByteArray()).joinToString("") { "%02x".format(it) }
        return File(dir, "chats-${hash.take(24)}.json")
    }
}
