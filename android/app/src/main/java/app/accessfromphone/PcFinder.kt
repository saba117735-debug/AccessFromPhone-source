package app.accessfromphone

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import java.util.concurrent.ConcurrentHashMap

/**
 * Looks for the PC on the Wi-Fi. The PC announces itself (mDNS, "_accessfromphone._tcp"), so the first screen needs only the
 * code, like the page in a browser. If the network hides it (some office Wi-Fi does), the page asks for the address instead.
 */
class PcFinder(context: Context) {
    data class Found(val name: String, val origin: String)

    private val nsd = context.applicationContext.getSystemService(Context.NSD_SERVICE) as NsdManager
    private val found = ConcurrentHashMap<String, Found>()
    private val waiting = ArrayDeque<NsdServiceInfo>()
    private var resolving = false
    private var listener: NsdManager.DiscoveryListener? = null

    fun found(): List<Found> = found.values.sortedBy { it.name }

    @Synchronized
    fun start() {
        if (listener != null) return
        found.clear()
        val l = object : NsdManager.DiscoveryListener {
            override fun onServiceFound(info: NsdServiceInfo) { if (info.serviceType.contains("_accessfromphone")) enqueue(info) }
            override fun onServiceLost(info: NsdServiceInfo) { found.remove(info.serviceName) }
            override fun onDiscoveryStarted(type: String) {}
            override fun onDiscoveryStopped(type: String) {}
            override fun onStartDiscoveryFailed(type: String, code: Int) { listener = null }
            override fun onStopDiscoveryFailed(type: String, code: Int) {}
        }
        listener = l
        runCatching { nsd.discoverServices(TYPE, NsdManager.PROTOCOL_DNS_SD, l) }.onFailure { listener = null }
    }

    @Synchronized
    fun stop() {
        listener?.let { runCatching { nsd.stopServiceDiscovery(it) } }
        listener = null
        waiting.clear()
    }

    // Android resolves one service at a time.
    @Synchronized private fun enqueue(info: NsdServiceInfo) { waiting.addLast(info); next() }

    @Synchronized private fun next() {
        if (resolving) return
        val info = waiting.removeFirstOrNull() ?: return
        resolving = true
        runCatching {
            nsd.resolveService(info, object : NsdManager.ResolveListener {
                override fun onResolveFailed(info: NsdServiceInfo, code: Int) = finished()
                override fun onServiceResolved(info: NsdServiceInfo) {
                    val secure = info.attributes["https"]?.let { String(it) } != "0"
                    Pairing.originFor(info.host?.hostAddress ?: "", info.port, secure)?.let { found[info.serviceName] = Found(info.serviceName, it) }
                    finished()
                }
            })
        }.onFailure { finished() }
    }

    @Synchronized private fun finished() { resolving = false; next() }

    companion object { const val TYPE = "_accessfromphone._tcp." }
}
