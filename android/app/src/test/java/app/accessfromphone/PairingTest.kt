package app.accessfromphone

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PairingTest {
    // ---- an address plus the typed code (the first screen's boxes)

    @Test
    fun `an address and a typed code connect like a scan`() {
        val t = Pairing.fromAddressAndCode("192.168.1.20:8765", "7kcx g7za")!!
        assertEquals("https://192.168.1.20:8765", t.origin)
        assertEquals("https://192.168.1.20:8765/#pair=7KCX-G7ZA", t.url)
    }

    @Test
    fun `an incomplete code or a bad address does not connect`() {
        assertNull(Pairing.fromAddressAndCode("192.168.1.20:8765", "7KCX-G7"))
        assertNull(Pairing.fromAddressAndCode("", "7KCX-G7ZA"))
        assertNull(Pairing.fromAddressAndCode("not an address", "7KCX-G7ZA"))
    }

    @Test
    fun `a PC found on the Wi-Fi becomes its address`() {
        assertEquals("https://192.168.1.20:8765", Pairing.originFor("192.168.1.20", 8765, true))
        assertEquals("http://192.168.1.20:8765", Pairing.originFor("192.168.1.20", 8765, false))
        assertEquals("https://[fe80::1]:8765", Pairing.originFor("fe80::1%wlan0", 8765, true))
        assertNull(Pairing.originFor("", 8765, true))
        assertNull(Pairing.originFor("192.168.1.20", 0, true))
        assertNull(Pairing.originFor("evil.com/x", 8765, true))
    }

    @Test
    fun `pasted text is tidied into a code`() {
        assertEquals("7KCXG7ZA", Pairing.cleanCode(" 7kcx-g7za\n"))
        assertEquals("ABCD1234", Pairing.cleanCode("abcd1234999"))
        assertEquals("", Pairing.cleanCode("--  "))
    }

    // ---- the QR code on the PC

    @Test
    fun `a code from the PC on the Wi-Fi is accepted`() {
        val t = Pairing.fromQr("https://10.157.162.14:8765/?pair=7KCX-G7ZA")
        assertNotNull(t)
        assertEquals("https://10.157.162.14:8765", t!!.origin)
        assertEquals("https://10.157.162.14:8765/#pair=7KCX-G7ZA", t.url)  // loaded with the code after the #, even from an older PC's ?pair= code
    }

    @Test
    fun `a code with the pairing part after the hash is accepted`() {
        val t = Pairing.fromQr("https://10.157.162.14:8765/#pair=7KCX-G7ZA")
        assertNotNull(t)
        assertEquals("https://10.157.162.14:8765", t!!.origin)
        assertEquals("https://10.157.162.14:8765/#pair=7KCX-G7ZA", t.url)
        assertNull(Pairing.fromQr("https://example.com/#pair=short"))
        assertNull(Pairing.fromQr("https://example.com/#/settings"))
    }

    @Test
    fun `a code from the relay is accepted and its address is kept`() {
        val t = Pairing.fromQr("https://abc123def456.xyz.com/?pair=ABCD-2345")
        assertEquals("https://abc123def456.xyz.com", t!!.origin)
    }

    @Test
    fun `surrounding spaces and a missing dash are fine`() {
        assertNotNull(Pairing.fromQr("  http://192.168.1.5:8765/?pair=ABCD2345\n"))
    }

    @Test
    fun `things that are not the PC's code are refused`() {
        assertNull(Pairing.fromQr("https://example.com/"))  // no pairing code
        assertNull(Pairing.fromQr("https://example.com/?pair=short"))  // wrong shape
        assertNull(Pairing.fromQr("https://example.com/?pair=ABCD-2345-EXTRA"))
        assertNull(Pairing.fromQr("javascript:alert(1)//?pair=ABCD-2345"))
        assertNull(Pairing.fromQr("ftp://10.0.0.1/?pair=ABCD-2345"))
        assertNull(Pairing.fromQr("WIFI:S:home;T:WPA;P:secret;;"))  // a different kind of QR code
        assertNull(Pairing.fromQr(""))
    }

    @Test
    fun `the code cannot smuggle in a second address`() {
        val t = Pairing.fromQr("https://10.0.0.5:8765/?pair=ABCD-2345&next=https://evil.example")
        assertEquals("https://10.0.0.5:8765/#pair=ABCD-2345", t!!.url)  // only the origin and the code are used
    }

    // ---- a typed address

    @Test
    fun `a typed address gets https and loses the trailing slash`() {
        assertEquals("https://192.168.1.20:8765", Pairing.fromAddress("192.168.1.20:8765/")!!.origin)
        assertEquals("https://abc123def456.xyz.com/", Pairing.fromAddress("abc123def456.xyz.com")!!.url)
    }

    @Test
    fun `an explicit http is respected and nonsense is refused`() {
        assertEquals("http://10.0.0.5:8765", Pairing.fromAddress("http://10.0.0.5:8765")!!.origin)
        assertNull(Pairing.fromAddress("two words"))
        assertNull(Pairing.fromAddress(""))
        assertNull(Pairing.fromAddress("ftp://x.example"))
    }

    // ---- which addresses are "your own network"

    @Test
    fun `private addresses are recognised`() {
        for (h in listOf("10.1.2.3", "192.168.0.9", "172.16.0.1", "172.31.255.1", "127.0.0.1", "169.254.1.1", "localhost", "abc.localhost", "my-pc.local")) {
            assertTrue(h, Pairing.isPrivate(h))
        }
    }

    @Test
    fun `public addresses are not`() {
        for (h in listOf("8.8.8.8", "172.32.0.1", "192.169.0.1", "10.0.0.256", "xyz.com", "abc123def456.xyz.com", "evil.local.example.com", "")) {
            assertFalse(h, Pairing.isPrivate(h))
        }
    }

    // ---- the certificate of the PC's own address

    @Test
    fun `the first certificate from your own PC is trusted and remembered`() {
        assertEquals(Pinning.Verdict.TRUST_AND_REMEMBER, Pinning.decide("10.0.0.5", "10.0.0.5", null, "aa"))
    }

    @Test
    fun `the same certificate afterwards is trusted and a different one is not`() {
        assertEquals(Pinning.Verdict.TRUST, Pinning.decide("10.0.0.5", "10.0.0.5", "aa", "aa"))
        assertEquals(Pinning.Verdict.REFUSE, Pinning.decide("10.0.0.5", "10.0.0.5", "aa", "bb"))
    }

    @Test
    fun `no exception is ever made for another site or a name on the internet`() {
        assertEquals(Pinning.Verdict.REFUSE, Pinning.decide("10.0.0.99", "10.0.0.5", null, "aa"))  // not the paired PC
        assertEquals(Pinning.Verdict.REFUSE, Pinning.decide("abc123def456.xyz.com", "abc123def456.xyz.com", null, "aa"))  // must have a real certificate
        assertEquals(Pinning.Verdict.REFUSE, Pinning.decide("10.0.0.5", null, null, "aa"))  // nothing paired yet
    }
}
