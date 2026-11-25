package app.accessfromphone

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class SavedChatsTest {
    @get:Rule val folder = TemporaryFolder()
    private fun store(max: Int = 1000) = SavedChats(folder.newFolder("chats"), max)

    @Test fun `what is saved is read back exactly`() {
        val s = store()
        assertTrue(s.write("https://pc:8765", """{"v":1,"jobs":[],"text":"héllo ✓"}"""))
        assertEquals("""{"v":1,"jobs":[],"text":"héllo ✓"}""", s.read("https://pc:8765"))
        assertTrue(s.has("https://pc:8765"))
    }

    @Test fun `nothing saved reads as empty`() {
        val s = store()
        assertEquals("", s.read("https://pc:8765"))
        assertFalse(s.has("https://pc:8765"))
        assertFalse(s.has(null))
        assertFalse(s.has(""))
    }

    @Test fun `saving again replaces the old copy`() {
        val s = store()
        s.write("a", "one")
        s.write("a", "two")
        assertEquals("two", s.read("a"))
    }

    @Test fun `each PC has its own copy`() {
        val s = store()
        s.write("https://one:8765", "first")
        s.write("https://two:8765", "second")
        assertEquals("first", s.read("https://one:8765"))
        assertEquals("second", s.read("https://two:8765"))
        s.clear("https://one:8765")
        assertEquals("", s.read("https://one:8765"))
        assertEquals("second", s.read("https://two:8765"))
    }

    @Test fun `the same address in other capitals is the same PC`() {
        val s = store()
        s.write("https://MY-PC:8765", "x")
        assertEquals("x", s.read("https://my-pc:8765"))
    }

    @Test fun `something over the limit is refused and the old copy is kept`() {
        val s = store(max = 10)
        assertTrue(s.write("a", "short"))
        assertFalse(s.write("a", "this is far too long"))
        assertEquals("short", s.read("a"))
    }

    @Test fun `the limit counts bytes, not characters`() {
        val s = store(max = 10)
        assertFalse(s.write("a", "✓✓✓✓✓"))  // 5 characters, 15 bytes
    }

    @Test fun `empty or nameless saves are refused`() {
        val s = store()
        assertFalse(s.write("a", ""))
        assertFalse(s.write("", "x"))
        assertFalse(s.write("  ", "x"))
    }

    @Test fun `clearing removes it and clearing nothing is harmless`() {
        val s = store()
        s.write("a", "x")
        s.clear("a")
        assertFalse(s.has("a"))
        s.clear("a")
        s.clear(null)
        s.clear("")
    }

    @Test fun `clear all removes every copy`() {
        val s = store()
        s.write("a", "1"); s.write("b", "2")
        s.clearAll()
        assertFalse(s.has("a")); assertFalse(s.has("b"))
    }

    @Test fun `no temporary file is left behind`() {
        val dir = folder.newFolder("tidy")
        val s = SavedChats(dir)
        s.write("a", "x"); s.write("a", "y")
        assertEquals(1, dir.listFiles()!!.size)
        assertFalse(dir.listFiles()!!.any { it.name.endsWith(".tmp") })
    }

    @Test fun `the file name does not give away the address`() {
        val dir = folder.newFolder("names")
        SavedChats(dir).write("https://secret-pc.example:8765", "x")
        val name = dir.listFiles()!!.single().name
        assertFalse(name.contains("secret"))
        assertNotEquals("chats-.json", name)
    }
}
