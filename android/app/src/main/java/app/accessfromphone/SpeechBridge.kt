package app.accessfromphone

import android.content.Intent
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.webkit.JavascriptInterface
import android.webkit.WebView
import org.json.JSONObject

/**
 * Voice input for the page. Android's web view has no SpeechRecognition, so the page is given one (see afp-shim.js)
 * that talks to this class, which uses the phone's own recogniser.
 */
class SpeechBridge(private val activity: MainActivity, private val web: WebView) {
    private var recognizer: SpeechRecognizer? = null

    @JavascriptInterface
    fun available(): Boolean = SpeechRecognizer.isRecognitionAvailable(activity)

    @JavascriptInterface
    fun start(lang: String, interim: Boolean) {
        activity.runOnUiThread {
            if (!activity.isPaired(web.url)) return@runOnUiThread
            activity.withMicrophone { granted ->
                if (granted) begin(lang, interim) else emit("error", "not-allowed").also { emit("end") }
            }
        }
    }

    @JavascriptInterface
    fun stop() = activity.runOnUiThread { recognizer?.stopListening() }

    @JavascriptInterface
    fun abort() = activity.runOnUiThread { recognizer?.cancel(); release() }

    fun release() {
        recognizer?.destroy()
        recognizer = null
    }

    private fun begin(lang: String, interim: Boolean) {
        release()
        val r = SpeechRecognizer.createSpeechRecognizer(activity)
        recognizer = r
        r.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) = emit("start")
            override fun onPartialResults(partial: Bundle?) = text(partial)?.let { emit("result", it, false) } ?: Unit
            override fun onResults(results: Bundle?) {
                text(results)?.let { emit("result", it, true) }
                emit("end")
            }
            override fun onError(error: Int) {
                emit("error", when (error) {
                    SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "no-speech"
                    SpeechRecognizer.ERROR_AUDIO -> "audio-capture"
                    SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "not-allowed"
                    SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT, SpeechRecognizer.ERROR_SERVER -> "network"
                    SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED, SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE -> "language-not-supported"
                    else -> "aborted"
                })
                emit("end")
            }
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {}
            override fun onEvent(eventType: Int, params: Bundle?) {}
        })
        r.startListening(Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, lang)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, interim)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
        })
    }

    private fun text(bundle: Bundle?): String? = bundle?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()

    private fun emit(type: String, text: String = "", final: Boolean = false) {
        val js = "window.__afpSpeech&&window.__afpSpeech(${JSONObject.quote(type)},${JSONObject.quote(text)},$final)"
        web.post { web.evaluateJavascript(js, null) }
    }
}
