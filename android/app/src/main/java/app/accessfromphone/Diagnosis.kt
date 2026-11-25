package app.accessfromphone

import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.SocketTimeoutException
import java.net.UnknownHostException

/**
 * Why the PC can't be reached, in words a person can act on. Plain Kotlin (no Android classes) so every case is unit-tested.
 * The app checks the connection itself within seconds instead of waiting on the web view's own, very long, timeout.
 */
object Diagnosis {
    /** What this phone is connected to. */
    enum class Net { NONE, WIFI, CELLULAR, OTHER }

    /** What happened when the app tried to open a connection to the PC. */
    enum class Probe { OK, REFUSED, TIMEOUT, NO_HOST, UNREACHABLE, ERROR }

    enum class Kind {
        PHONE_OFFLINE,    // this phone has no connection at all
        OTHER_NETWORK,    // the PC is on a home/office network and this phone is on mobile data
        APP_NOT_RUNNING,  // the PC is on and reachable, but the app isn't running there
        PC_UNREACHABLE,   // on the PC's own network, but it doesn't answer: off, asleep, or another Wi-Fi
        PC_OFFLINE,       // through the relay: the PC is off, asleep, or has no internet
        NAME_NOT_FOUND,   // the PC's address can't be looked up
        PC_ERROR,         // the PC answered, with an error
        ADDRESS_CHANGED,  // the PC is on this Wi-Fi, but at a different address than the one saved
        CERTIFICATE,      // the PC's security certificate changed
    }

    /** What is wrong, or null when the PC can be reached. `privateHost` = the PC's address is on a home/office network. */
    fun diagnose(net: Net, privateHost: Boolean, probe: Probe, movedTo: Boolean = false): Kind? = when {
        net == Net.NONE -> Kind.PHONE_OFFLINE
        probe == Probe.OK -> null  // even on mobile data: a VPN may be carrying it
        movedTo -> Kind.ADDRESS_CHANGED  // the saved address is dead, yet the PC answers on this Wi-Fi at another one
        probe == Probe.REFUSED -> Kind.APP_NOT_RUNNING
        privateHost && net == Net.CELLULAR -> Kind.OTHER_NETWORK
        probe == Probe.NO_HOST -> if (privateHost) Kind.PC_UNREACHABLE else Kind.NAME_NOT_FOUND
        privateHost -> Kind.PC_UNREACHABLE
        else -> Kind.PC_OFFLINE
    }

    /** The PC (or the relay in front of it) answered the page request with an error. */
    fun fromHttp(status: Int, privateHost: Boolean): Kind? = when (status) {
        502, 503, 504 -> if (privateHost) Kind.PC_UNREACHABLE else Kind.PC_OFFLINE  // the relay says the PC isn't there, or didn't answer
        in 500..599 -> Kind.PC_ERROR
        else -> null
    }

    /** The web view's own error codes (WebViewClient.ERROR_*), when the page failed to load. */
    fun fromWebError(code: Int): Probe = when (code) {
        -2 -> Probe.NO_HOST       // ERROR_HOST_LOOKUP
        -8 -> Probe.TIMEOUT       // ERROR_TIMEOUT
        -6, -7 -> Probe.UNREACHABLE  // ERROR_CONNECT, ERROR_IO
        else -> Probe.ERROR
    }

    fun classify(error: Throwable): Probe = when {
        error is UnknownHostException -> Probe.NO_HOST
        error is SocketTimeoutException -> Probe.TIMEOUT
        error is NoRouteToHostException -> Probe.UNREACHABLE
        error is ConnectException -> if (error.message.orEmpty().contains("refused", ignoreCase = true)) Probe.REFUSED else Probe.UNREACHABLE
        else -> Probe.ERROR
    }

    /** Worth trying again by itself: waiting can fix it. A changed certificate must be a person's decision. */
    fun retries(kind: Kind): Boolean = kind != Kind.CERTIFICATE
}
