package app.accessfromphone

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class LocalPageTest {
    @Test fun `the page and its files are served`() {
        assertEquals("index.html", LocalPage.assetFor(""))
        assertEquals("index.html", LocalPage.assetFor("/"))
        assertEquals("app.js", LocalPage.assetFor("static/app.js"))
        assertEquals("app.css", LocalPage.assetFor("static/app.css"))
        assertEquals("conn.js", LocalPage.assetFor("static/conn.js"))
        assertEquals("offline.js", LocalPage.assetFor("static/offline.js"))  // the saved chats the page reads when the PC is off
        assertEquals("icon.svg", LocalPage.assetFor("static/icon.svg"))
    }

    @Test fun `nothing else is ever served`() {
        for (bad in listOf("sw.js", "static/../index.html", "../secret", "static/vendor/jsQR.js", "web/app.js", "static/manifest.webmanifest")) assertNull(bad, LocalPage.assetFor(bad))
    }

    @Test fun `files are sent as the right kind`() {
        assertEquals("text/html", LocalPage.mimeOf("index.html"))
        assertEquals("text/javascript", LocalPage.mimeOf("app.js"))
        assertEquals("text/css", LocalPage.mimeOf("app.css"))
        assertEquals("image/svg+xml", LocalPage.mimeOf("icon.svg"))
    }

    @Test fun `only the built-in address counts as local`() {
        assertTrue(LocalPage.isLocal("https://appassets.androidplatform.net/"))
        assertTrue(LocalPage.isLocal("https://appassets.androidplatform.net/static/app.js"))
        assertFalse(LocalPage.isLocal("https://appassets.androidplatform.net.evil.com/"))
        assertFalse(LocalPage.isLocal("https://192.168.1.20:8765/"))
        assertFalse(LocalPage.isLocal(null))
    }
}
