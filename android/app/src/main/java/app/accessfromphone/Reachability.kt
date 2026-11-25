package app.accessfromphone

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URI

/** The parts of "can I reach the PC?" that need Android: what this phone is connected to, and a quick test connection. */
object Reachability {
    fun network(context: Context): Diagnosis.Net {
        val manager = context.getSystemService(ConnectivityManager::class.java) ?: return Diagnosis.Net.OTHER
        val active = manager.activeNetwork ?: return Diagnosis.Net.NONE
        val caps = manager.getNetworkCapabilities(active) ?: return Diagnosis.Net.NONE
        return when {
            caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> Diagnosis.Net.WIFI
            caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> Diagnosis.Net.CELLULAR
            else -> Diagnosis.Net.OTHER
        }
    }

    /** Opens (and at once closes) a connection to the PC's address, so a PC that is off is found out in seconds, not minutes. */
    fun probe(origin: String, timeoutMs: Int = 4000): Diagnosis.Probe {
        val uri = runCatching { URI(origin) }.getOrNull() ?: return Diagnosis.Probe.ERROR
        val host = uri.host ?: return Diagnosis.Probe.ERROR
        val port = if (uri.port != -1) uri.port else if (uri.scheme == "https") 443 else 80
        return try {
            Socket().use { it.connect(InetSocketAddress(host, port), timeoutMs) }
            Diagnosis.Probe.OK
        } catch (e: Exception) {
            Diagnosis.classify(e)
        }
    }

    /** Calls `changed` whenever the phone's connection changes (Wi-Fi comes back, mobile data starts). Returns how to stop. */
    fun onNetworkChange(context: Context, changed: () -> Unit): () -> Unit {
        val manager = context.getSystemService(ConnectivityManager::class.java) ?: return {}
        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) = changed()
        }
        val stop: () -> Unit = { runCatching { manager.unregisterNetworkCallback(callback) } }
        return if (runCatching { manager.registerDefaultNetworkCallback(callback) }.isSuccess) stop else ({})
    }
}
