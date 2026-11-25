package app.accessfromphone

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PushLinkTest {
    @Test fun `a plain request id is accepted`() {
        assertEquals("ab12cd34", PushLink.jobId("ab12cd34"))
        assertEquals("a-b_c", PushLink.jobId("a-b_c"))
    }

    @Test fun `anything that is not a plain id is refused`() {
        for (bad in listOf("", "a b", "x';alert(1)//", "../x", "a\nb", "a".repeat(41), "<b>")) assertNull(bad, PushLink.jobId(bad))
        assertNull(PushLink.jobId(null))
    }

    @Test fun `the script that opens a request can only hold a checked id`() {
        assertEquals("location.hash='#job/ab12cd34'", PushLink.openScript("ab12cd34"))
        assertNull(PushLink.openScript("1';alert(1);'"))
    }

    @Test fun `a notification needs some text, and is cut to a sensible size`() {
        assertNull(PushLink.fromData(emptyMap()))
        assertNull(PushLink.fromData(mapOf("title" to "  ", "body" to "")))
        val note = PushLink.fromData(mapOf("title" to "Plan ready – tap to approve", "body" to "x".repeat(900), "job" to "ab12cd34", "kind" to "awaiting"))!!
        assertEquals("Plan ready – tap to approve", note.title)
        assertEquals(240, note.body.length)
        assertEquals("ab12cd34", note.job)
    }

    @Test fun `a bad request id in a notification is dropped, not trusted`() {
        val note = PushLink.fromData(mapOf("title" to "Job finished", "job" to "x';alert(1)//"))!!
        assertEquals("", note.job)
    }

    @Test fun `text handed to the page cannot break out of its string`() {
        val s = PushLink.jsString("a\"b\\c\n</script> ")
        assertEquals("\"a\\\"b\\\\c\\n\\u003c/script\\u003e\\u2028\"", s)
        assertFalse(s.contains("<"))
        assertFalse(s.contains("\n"))
    }

    @Test fun `the page is told what the app found out about the connection`() {
        assertEquals("window.dispatchEvent(new CustomEvent('afp-diagnosis',{detail:{kind:\"PC_OFFLINE\",powerSave:true}}))", PushLink.diagnosisScript("PC_OFFLINE", true))
        assertEquals(true, PushLink.diagnosisScript("x\");alert(1);//", false).contains("kind:\"PC_UNREACHABLE\""))  // anything odd becomes a plain word
    }

    @Test fun `a notification that arrives while the app is open tells the page what it was`() {
        assertEquals("window.dispatchEvent(new CustomEvent('afp-nudge',{detail:{kind:\"pc_sleep\"}}))", PushLink.nudgeScript("pc_sleep"))
        assertEquals("window.dispatchEvent(new CustomEvent('afp-nudge',{detail:{kind:\"\"}}))", PushLink.nudgeScript("x';alert(1)"))
    }

    @Test fun `the page is told the token, or why there is none`() {
        assertEquals("window.dispatchEvent(new CustomEvent('afp-push',{detail:{token:\"abc:DEF-_1\"}}))", PushLink.tokenScript("abc:DEF-_1", null))
        val err = PushLink.tokenScript(null, "It's \"off\"")
        assertTrue(err.contains("{error:\"It's \\\"off\\\"\"}"))
    }
}
