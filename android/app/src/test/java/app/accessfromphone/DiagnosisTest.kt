package app.accessfromphone

import app.accessfromphone.Diagnosis.Kind
import app.accessfromphone.Diagnosis.Net
import app.accessfromphone.Diagnosis.Probe
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.SocketException
import java.net.SocketTimeoutException
import java.net.UnknownHostException

class DiagnosisTest {
    @Test fun `a phone with no connection says so, whatever else is true`() {
        for (probe in Probe.values()) assertEquals(Kind.PHONE_OFFLINE, Diagnosis.diagnose(Net.NONE, true, probe))
        assertEquals(Kind.PHONE_OFFLINE, Diagnosis.diagnose(Net.NONE, false, Probe.TIMEOUT))
    }

    @Test fun `a reachable PC is no problem, on any network`() {
        for (net in listOf(Net.WIFI, Net.CELLULAR, Net.OTHER)) for (private in listOf(true, false)) assertNull(Diagnosis.diagnose(net, private, Probe.OK))
    }

    @Test fun `a PC that is on but not running the app is told apart from a PC that is off`() {
        assertEquals(Kind.APP_NOT_RUNNING, Diagnosis.diagnose(Net.WIFI, true, Probe.REFUSED))
        assertEquals(Kind.APP_NOT_RUNNING, Diagnosis.diagnose(Net.CELLULAR, false, Probe.REFUSED))
    }

    @Test fun `on the home Wi-Fi a silent PC is off, asleep or on another network`() {
        for (probe in listOf(Probe.TIMEOUT, Probe.UNREACHABLE, Probe.ERROR, Probe.NO_HOST)) assertEquals(Kind.PC_UNREACHABLE, Diagnosis.diagnose(Net.WIFI, true, probe))
    }

    @Test fun `a home address from mobile data means the phone is on the wrong network`() {
        assertEquals(Kind.OTHER_NETWORK, Diagnosis.diagnose(Net.CELLULAR, true, Probe.TIMEOUT))
        assertEquals(Kind.OTHER_NETWORK, Diagnosis.diagnose(Net.CELLULAR, true, Probe.UNREACHABLE))
    }

    @Test fun `through the relay a silent PC is offline, and an unknown name is a different message`() {
        assertEquals(Kind.PC_OFFLINE, Diagnosis.diagnose(Net.WIFI, false, Probe.TIMEOUT))
        assertEquals(Kind.PC_OFFLINE, Diagnosis.diagnose(Net.CELLULAR, false, Probe.UNREACHABLE))
        assertEquals(Kind.NAME_NOT_FOUND, Diagnosis.diagnose(Net.WIFI, false, Probe.NO_HOST))
    }

    @Test fun `a PC that moved to a new address on the same Wi-Fi is told apart from one that is off`() {
        assertEquals(Kind.ADDRESS_CHANGED, Diagnosis.diagnose(Net.WIFI, true, Probe.TIMEOUT, movedTo = true))
        assertEquals(Kind.ADDRESS_CHANGED, Diagnosis.diagnose(Net.WIFI, true, Probe.REFUSED, movedTo = true))
        assertNull(Diagnosis.diagnose(Net.WIFI, true, Probe.OK, movedTo = true))                      // still answering at the old address: fine
        assertEquals(Kind.PHONE_OFFLINE, Diagnosis.diagnose(Net.NONE, true, Probe.ERROR, movedTo = true))  // and no network beats everything
    }

    @Test fun `the relay's offline answers and the PC's own errors are told apart`() {
        assertEquals(Kind.PC_OFFLINE, Diagnosis.fromHttp(503, false))
        assertEquals(Kind.PC_OFFLINE, Diagnosis.fromHttp(502, false))
        assertEquals(Kind.PC_OFFLINE, Diagnosis.fromHttp(504, false))
        assertEquals(Kind.PC_UNREACHABLE, Diagnosis.fromHttp(503, true))
        assertEquals(Kind.PC_ERROR, Diagnosis.fromHttp(500, true))
        assertNull(Diagnosis.fromHttp(404, false))
        assertNull(Diagnosis.fromHttp(401, true))
        assertNull(Diagnosis.fromHttp(200, true))
    }

    @Test fun `connection errors are classified`() {
        assertEquals(Probe.NO_HOST, Diagnosis.classify(UnknownHostException("x")))
        assertEquals(Probe.TIMEOUT, Diagnosis.classify(SocketTimeoutException("connect timed out")))
        assertEquals(Probe.REFUSED, Diagnosis.classify(ConnectException("failed to connect: ECONNREFUSED (Connection refused)")))
        assertEquals(Probe.UNREACHABLE, Diagnosis.classify(ConnectException("failed to connect: ENETUNREACH (Network is unreachable)")))
        assertEquals(Probe.UNREACHABLE, Diagnosis.classify(NoRouteToHostException("No route to host")))
        assertEquals(Probe.ERROR, Diagnosis.classify(SocketException("something else")))
    }

    @Test fun `the web view's error codes are classified`() {
        assertEquals(Probe.NO_HOST, Diagnosis.fromWebError(-2))
        assertEquals(Probe.TIMEOUT, Diagnosis.fromWebError(-8))
        assertEquals(Probe.UNREACHABLE, Diagnosis.fromWebError(-6))
        assertEquals(Probe.ERROR, Diagnosis.fromWebError(-1))
    }

    @Test fun `every kind retries by itself except a changed certificate`() {
        for (kind in Kind.values()) assertEquals(kind != Kind.CERTIFICATE, Diagnosis.retries(kind))
        assertTrue(Diagnosis.retries(Kind.PC_OFFLINE))
        assertFalse(Diagnosis.retries(Kind.CERTIFICATE))
    }
}
