package app.accessfromphone

import android.net.http.SslCertificate
import java.security.MessageDigest

/**
 * The PC makes its own certificate for the Wi-Fi address, so no authority has signed it. Browsers show a scary warning;
 * this app instead remembers the first certificate it sees from that PC and accepts only that one afterwards.
 * If the certificate ever changes, it stops and says so.
 */
object Pinning {
    enum class Verdict { TRUST_AND_REMEMBER, TRUST, REFUSE }

    fun decide(host: String, pairedHost: String?, remembered: String?, seen: String): Verdict = when {
        host != pairedHost || !Pairing.isPrivate(host) -> Verdict.REFUSE  // never for another site, never for a name on the internet
        remembered == null -> Verdict.TRUST_AND_REMEMBER
        remembered == seen -> Verdict.TRUST
        else -> Verdict.REFUSE
    }

    fun fingerprint(cert: SslCertificate): String? {
        val der = SslCertificate.saveState(cert).getByteArray("x509-certificate") ?: return null
        return MessageDigest.getInstance("SHA-256").digest(der).joinToString("") { "%02x".format(it) }
    }
}
