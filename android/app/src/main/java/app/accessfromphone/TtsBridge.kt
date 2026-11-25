package app.accessfromphone

import android.content.Intent
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.webkit.JavascriptInterface
import android.webkit.WebView
import java.util.Locale

/**
 * Spoken replies for the page. Android's web view may not have speechSynthesis, and without it the page hides its speaker button,
 * so the page is given one (see afp-shim.js) that talks to this class, which uses the phone's own text-to-speech.
 */
class TtsBridge(private val activity: MainActivity, private val web: WebView) {
    private var tts: TextToSpeech? = null
    private var ready = false
    private var queued: Triple<String, String, Float>? = null  // asked for before the engine finished starting (main thread only)

    @JavascriptInterface
    fun available(): Boolean = activity.packageManager.queryIntentServices(Intent(TextToSpeech.Engine.INTENT_ACTION_TTS_SERVICE), 0).isNotEmpty()

    @JavascriptInterface
    fun speak(text: String, lang: String, rate: Float) {
        activity.runOnUiThread {
            if (!activity.isPaired(web.url) || text.isBlank()) return@runOnUiThread
            val engine = tts
            when {
                engine == null -> { queued = Triple(text, lang, rate); start() }
                ready -> say(engine, text, lang, rate)
                else -> queued = Triple(text, lang, rate)
            }
        }
    }

    @JavascriptInterface
    fun stop() = activity.runOnUiThread { queued = null; tts?.stop() }

    fun release() {
        tts?.shutdown()
        tts = null
        ready = false
    }

    private fun start() {
        tts = TextToSpeech(activity) { status ->
            activity.runOnUiThread {
                val engine = tts ?: return@runOnUiThread
                ready = status == TextToSpeech.SUCCESS
                if (!ready) { emit("error"); queued = null; return@runOnUiThread }
                engine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                    override fun onStart(id: String?) = emit("start")
                    override fun onDone(id: String?) = emit("end")
                    @Deprecated("Deprecated in Java") override fun onError(id: String?) = emit("error")
                })
                queued?.let { (text, lang, rate) -> say(engine, text, lang, rate) }
                queued = null
            }
        }
    }

    private fun say(engine: TextToSpeech, text: String, lang: String, rate: Float) {
        runCatching { engine.language = Locale.forLanguageTag(lang) }
        engine.setSpeechRate(rate.coerceIn(0.5f, 2f))
        engine.speak(text.take(600), TextToSpeech.QUEUE_FLUSH, null, "afp")
    }

    private fun emit(type: String) = activity.runOnUiThread { web.evaluateJavascript("window.__afpTts&&window.__afpTts('$type')", null) }
}
