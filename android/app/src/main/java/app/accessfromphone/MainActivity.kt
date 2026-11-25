package app.accessfromphone

import android.Manifest
import android.annotation.SuppressLint
import android.content.ContentValues
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.net.Uri
import android.net.http.SslError
import android.hardware.biometrics.BiometricManager
import android.hardware.biometrics.BiometricPrompt
import android.os.Build
import android.os.CancellationSignal
import android.os.SystemClock
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.VibrationEffect
import android.os.Vibrator
import android.provider.Settings
import android.provider.MediaStore
import android.util.Base64
import android.view.View
import android.webkit.JavascriptInterface
import android.webkit.PermissionRequest
import android.webkit.SslErrorHandler
import android.webkit.URLUtil
import android.webkit.JsResult
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.TextView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.webkit.WebViewAssetLoader
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.io.File
import java.util.concurrent.Executors

/**
 * The whole app is one screen: the PC's own phone page in a web view, plus a native first screen to scan the PC's QR code.
 * Everything else (chat, approvals, editing) is the same page that opens in a phone's browser.
 */
class MainActivity : AppCompatActivity() {
    private lateinit var store: PcStore
    private lateinit var web: WebView
    private lateinit var problem: View
    private lateinit var loading: View
    private lateinit var speech: SpeechBridge
    private lateinit var tts: TtsBridge
    private lateinit var finder: PcFinder
    private lateinit var saved: SavedChats
    private var shim = ""
    private var micAnswer: ((Boolean) -> Unit)? = null
    private val io = Executors.newSingleThreadExecutor()

    private val micPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        micAnswer?.invoke(ok)
        micAnswer = null
    }
    private var notifAnswer: ((Boolean) -> Unit)? = null
    private var pendingJob: String? = null  // the request a tapped notification was about, opened once the page is showing
    private val notifPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        notifAnswer?.invoke(ok)
        notifAnswer = null
    }
    private val scanner = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        result.data?.getStringExtra(ScanActivity.RESULT_URL)?.let { Pairing.fromQr(it) }?.let { connect(it) }
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        store = PcStore(this)
        shim = assets.open("afp-shim.js").bufferedReader().use { it.readText() }

        val root = findViewById<View>(R.id.root)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        WindowCompat.getInsetsController(window, root).isAppearanceLightStatusBars = false
        ViewCompat.setOnApplyWindowInsetsListener(root) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime())
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            WindowInsetsCompat.CONSUMED
        }

        web = findViewById(R.id.web)
        problem = findViewById(R.id.problem)
        loading = findViewById(R.id.loading)
        speech = SpeechBridge(this, web)
        tts = TtsBridge(this, web)
        finder = PcFinder(this)
        saved = SavedChats(File(filesDir, "chats"))

        findViewById<View>(R.id.saved_chats).setOnClickListener { openSavedChats() }
        findViewById<View>(R.id.retry).setOnClickListener { open(store.origin) }
        findViewById<View>(R.id.scan_again).setOnClickListener { scanner.launch(Intent(this, ScanActivity::class.java)) }
        findViewById<View>(R.id.other_pc).setOnClickListener { forgetPc() }
        findViewById<View>(R.id.go_back).setOnClickListener { if (store.revert()) { shimInstalledFor = null; open(store.origin) } }

        configureWebView()
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                when {
                    offlineView -> { offlineView = false; open(store.origin) }  // reading saved chats: Back returns to the "can't reach your PC" screen
                    blocking -> moveTaskToBack(true)  // the "PC unreachable" popup can't be dismissed: Back just puts the app away
                    web.visibility == View.VISIBLE && web.canGoBack() -> web.goBack()
                    else -> {  // the bottom of the stack: a second Back within two seconds leaves, so one slip does not close the app
                        val now = SystemClock.elapsedRealtime()
                        if (now - lastBack < 2000) finish() else { lastBack = now; Toast.makeText(this@MainActivity, R.string.back_again, Toast.LENGTH_SHORT).show() }
                    }
                }
            }
        })
        pendingJob = PushLink.jobId(intent?.getStringExtra(Push.EXTRA_JOB))
        // When the phone's connection changes (Wi-Fi back, mobile data on) and the PC couldn't be reached, try again at once.
        stopWatchingNetwork = Reachability.onNetworkChange(this) {
            runOnUiThread {
                val kind = shownKind; val origin = store.origin
                if (offlineView) leaveSavedChatsIfReachable(manual = false)
                else if (kind != null && origin != null && Diagnosis.retries(kind)) open(origin, quiet = true)
            }
        }
        open(store.origin)
    }

    /** A notification was tapped while the app was already open. */
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        pendingJob = PushLink.jobId(intent.getStringExtra(Push.EXTRA_JOB))
        deliverPendingJob()
    }

    private fun deliverPendingJob() {
        val script = PushLink.openScript(pendingJob) ?: return
        if (!isPaired(web.url) || web.visibility != View.VISIBLE) return  // still loading: onPageFinished tries again
        pendingJob = null
        web.evaluateJavascript(script, null)
    }

    /** Firebase gave this phone a new address: the page hands it to the PC. */
    fun sendPushToken(token: String) {
        if (isPaired(web.url)) web.evaluateJavascript(PushLink.tokenScript(token, null), null)
    }

    /** A notification arrived while the app is open in front of the person: the page refreshes instead of showing a banner. */
    fun nudge(kind: String) {
        if (isPaired(web.url)) web.evaluateJavascript(PushLink.nudgeScript(kind), null)
    }

    private var lastBack = 0L                         // when Back was last pressed at the bottom of the stack (double-Back to exit)
    @Volatile private var blocking = false        // the page's "PC unreachable" popup is up: nothing may get past it
    @Volatile private var certRefusedAt = 0L       // when the app last refused a PC because its certificate changed

    private fun buzz(kind: String) {
        runCatching {
            val vibrator = getSystemService(Vibrator::class.java) ?: return
            if (vibrator.hasVibrator()) vibrator.vibrate(VibrationEffect.createWaveform(Haptics.waveform(kind), -1))
        }
    }

    /** Looks at the connection (what network, is the PC answering, did its certificate change, has it moved) and tells the page. */
    private fun diagnoseNow() {
        val origin = store.origin ?: return
        io.execute {
            val net = Reachability.network(this)
            val powerSave = getSystemService(PowerManager::class.java)?.isPowerSaveMode == true
            val probe = if (net == Diagnosis.Net.NONE) Diagnosis.Probe.ERROR else Reachability.probe(origin)
            val kind: String = when {
                System.currentTimeMillis() - certRefusedAt < 120_000 -> Diagnosis.Kind.CERTIFICATE.name
                else -> {
                    // The saved address is dead: is the PC perhaps on this same Wi-Fi at a new one? (It announces itself.)
                    val moved = net == Diagnosis.Net.WIFI && probe != Diagnosis.Probe.OK && !onLocalPage && movedElsewhere(origin)
                    Diagnosis.diagnose(net, isPrivateOrigin(origin), probe, moved)?.name ?: "OK"
                }
            }
            runOnUiThread { web.evaluateJavascript(PushLink.diagnosisScript(kind, powerSave), null) }
        }
    }

    private fun movedElsewhere(origin: String): Boolean {
        finder.start()
        try {
            repeat(10) {
                if (finder.found().any { it.origin != origin }) return true
                Thread.sleep(300)
            }
            return false
        } finally {
            finder.stop()
        }
    }

    private fun withNotifications(answer: (Boolean) -> Unit) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU || hasPermission(Manifest.permission.POST_NOTIFICATIONS)) return answer(true)
        notifAnswer = answer
        notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
    }

    // ------------------------------------------------------------------ the web view

    @SuppressLint("SetJavaScriptEnabled")
    private fun configureWebView() {
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)
        web.setBackgroundColor(ContextCompat.getColor(this, R.color.bg))
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            allowFileAccess = false
            allowContentAccess = false
            setSupportZoom(false)
            userAgentString = userAgentString + " AccessFromPhoneApp/" + BuildConfig.VERSION_NAME
        }
        web.addJavascriptInterface(AppBridge(), "AfpApp")
        web.addJavascriptInterface(speech, "AfpSpeech")
        web.addJavascriptInterface(tts, "AfpTts")
        web.setDownloadListener { url, _, disposition, mime, _ ->
            if (url.startsWith("blob:")) {  // the page saves files from memory; the shim handles named ones, this catches the rest
                val name = URLUtil.guessFileName(url, disposition, mime)
                web.evaluateJavascript(
                    "fetch(${JSONObject.quote(url)}).then(r=>r.blob()).then(b=>{var f=new FileReader();f.onloadend=function(){AfpApp.saveFile(${JSONObject.quote(name)},b.type||'application/octet-stream',String(f.result).split(',')[1]||'')};f.readAsDataURL(b)})",
                    null,
                )
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            // Without these, a WebView silently answers "no" to every confirm() (and the page's "Close without saving?" or "Sign out?" could never be agreed to).
            override fun onJsConfirm(view: WebView, url: String, message: String, result: JsResult): Boolean {
                if (isFinishing) { result.cancel(); return true }
                AlertDialog.Builder(this@MainActivity).setMessage(message)
                    .setPositiveButton(android.R.string.ok) { _, _ -> result.confirm() }
                    .setNegativeButton(android.R.string.cancel) { _, _ -> result.cancel() }
                    .setOnCancelListener { result.cancel() }.show()
                return true
            }

            override fun onJsAlert(view: WebView, url: String, message: String, result: JsResult): Boolean {
                if (isFinishing) { result.confirm(); return true }
                AlertDialog.Builder(this@MainActivity).setMessage(message)
                    .setPositiveButton(android.R.string.ok) { _, _ -> result.confirm() }
                    .setOnCancelListener { result.confirm() }.show()
                return true
            }

            override fun onPermissionRequest(request: PermissionRequest) {
                runOnUiThread {
                    if (!isPaired(request.origin.toString().trimEnd('/'))) return@runOnUiThread request.deny()
                    val wantsMic = PermissionRequest.RESOURCE_AUDIO_CAPTURE in request.resources
                    if (wantsMic && !hasPermission(Manifest.permission.RECORD_AUDIO)) {
                        withMicrophone { ok -> if (ok) request.grant(request.resources) else request.deny() }
                    } else if (PermissionRequest.RESOURCE_VIDEO_CAPTURE in request.resources && !hasPermission(Manifest.permission.CAMERA)) {
                        request.deny()  // the app scans QR codes natively; the page never needs the camera
                    } else {
                        request.grant(request.resources)
                    }
                }
            }
        }
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val target = request.url.toString()
                if (isPaired(target) || LocalPage.isLocal(target) || target.startsWith("blob:") || target == "about:blank") return false
                runCatching { startActivity(Intent(Intent.ACTION_VIEW, request.url)) }  // any other site opens in the real browser
                return true
            }

            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? =
                localPages.shouldInterceptRequest(request.url)

            override fun onPageStarted(view: WebView, url: String, favicon: Bitmap?) {
                pcPage = isPaired(url)
                loading.visibility = View.VISIBLE
                if (!WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT) && isPaired(url)) view.evaluateJavascript(shim, null)
            }

            override fun onPageFinished(view: WebView, url: String) {
                loading.visibility = View.GONE
                if (shownKind != null) return  // an error page is behind the problem panel: keep the panel
                if (isPaired(url) || LocalPage.isLocal(url)) {
                    pageDone = true; showWeb(); deliverPendingJob()
                    if (isPaired(url) && store.previous != null && store.previous != store.origin) store.clearPrevious()  // the switch worked: nothing to go back to
                }
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (!request.isForMainFrame || LocalPage.isLocal(request.url.toString())) return
                val origin = store.origin
                val kind = Diagnosis.diagnose(Reachability.network(this@MainActivity), isPrivateOrigin(origin), Diagnosis.fromWebError(error.errorCode))
                showKind(kind ?: Diagnosis.Kind.PC_UNREACHABLE, origin)
            }

            // The relay answers 503 "your PC is offline" when the PC isn't connected to it; the PC itself may answer with an error.
            override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, response: WebResourceResponse) {
                if (!request.isForMainFrame || !isPaired(request.url.toString())) return
                val kind = Diagnosis.fromHttp(response.statusCode, isPrivateOrigin(store.origin)) ?: return
                showKind(kind, store.origin)
            }

            override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
                val host = Uri.parse(error.url).host ?: return handler.cancel()
                val seen = Pinning.fingerprint(error.certificate) ?: return handler.cancel()
                when (Pinning.decide(host, Pairing.hostOf(store.origin ?: ""), store.pin, seen)) {
                    Pinning.Verdict.TRUST_AND_REMEMBER -> { store.pin = seen; handler.proceed() }
                    Pinning.Verdict.TRUST -> handler.proceed()
                    Pinning.Verdict.REFUSE -> {
                        handler.cancel()
                        certRefusedAt = System.currentTimeMillis()
                        showKind(Diagnosis.Kind.CERTIFICATE, store.origin)
                    }
                }
            }
        }
    }

    /** Only the PC this phone was paired with is ever shown, so the page may use the app's native helpers. */
    fun isPaired(url: String?): Boolean {
        val origin = store.origin ?: return false
        return url != null && (url == origin || url.startsWith("$origin/") || url.startsWith("$origin?"))
    }

    fun hasPermission(name: String) = ContextCompat.checkSelfPermission(this, name) == PackageManager.PERMISSION_GRANTED

    fun withMicrophone(answer: (Boolean) -> Unit) {
        if (hasPermission(Manifest.permission.RECORD_AUDIO)) return answer(true)
        micAnswer = answer
        micPermission.launch(Manifest.permission.RECORD_AUDIO)
    }

    // ------------------------------------------------------------------ which screen is showing

    /**
     * Opens the PC's page, but first checks the connection itself (a few seconds), so a PC that is off is explained at once
     * instead of leaving a blank screen while the web view waits out its own very long timeout.
     * `quiet` = a retry from the problem panel: no flashing, it just loads the page if the PC is back.
     */
    private fun open(origin: String?, quiet: Boolean = false) {
        if (origin == null) return showWelcome()
        onLocalPage = false
        offlineView = false
        blocking = false  // a fresh page has no popup
        val mine = ++attempt
        ui.removeCallbacksAndMessages(null)
        if (!quiet) showConnecting(origin)
        io.execute {
            val net = Reachability.network(this)
            val probe = if (net == Diagnosis.Net.NONE) Diagnosis.Probe.ERROR else Reachability.probe(origin)
            val kind = Diagnosis.diagnose(net, isPrivateOrigin(origin), probe)
            runOnUiThread {
                if (mine != attempt) return@runOnUiThread
                if (kind != null) showKind(kind, origin) else load(origin, mine)
            }
        }
    }

    private fun load(origin: String, mine: Int) {
        shownKind = null
        pageDone = false
        installShim(origin)
        web.loadUrl("$origin/")
        // The PC answered the test connection but the page still doesn't come: say so, instead of waiting forever.
        ui.postDelayed({
            if (mine == attempt && !pageDone && shownKind == null) {
                web.stopLoading()
                showKind(if (isPrivateOrigin(origin)) Diagnosis.Kind.PC_UNREACHABLE else Diagnosis.Kind.PC_OFFLINE, origin)
            }
        }, 20_000)
    }

    private fun connect(target: Pairing.Target) {
        onLocalPage = false
        offlineView = false
        blocking = false
        attempt++
        shownKind = null
        pageDone = false
        ui.removeCallbacksAndMessages(null)
        finder.stop()
        store.origin = target.origin
        showLoading()
        installShim(target.origin)
        val name = deviceName.trim().take(40)
        // The page reads #pair=… (kept out of every server log) and ?name=…, and joins by itself.
        val pageUrl = when {
            name.isEmpty() -> target.url
            target.url.contains("/#pair=") -> target.url.replace("/#pair=", "/?name=" + Uri.encode(name) + "#pair=")
            else -> target.url + "?name=" + Uri.encode(name)
        }
        web.loadUrl(pageUrl)
        web.clearHistory()  // Back must not return to the connect screen
    }

    private var shimInstalledFor: String? = null

    private fun installShim(origin: String) {
        if (shimInstalledFor == origin || !WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) return
        runCatching { WebViewCompat.addDocumentStartJavaScript(web, shim, setOf(origin)) }.onSuccess { shimInstalledFor = origin }
    }

    /** No PC yet: show the page's own "Connect to your PC" screen, from the copy of the page built into the app. */
    private fun showWelcome() {
        onLocalPage = true
        offlineView = false
        blocking = false
        attempt++
        shownKind = null
        ui.removeCallbacksAndMessages(null)
        finder.start()  // so the code alone is enough: the PC announces itself on the Wi-Fi
        problem.visibility = View.GONE
        loading.visibility = View.VISIBLE
        web.loadUrl(LocalPage.URL)
    }

    private fun showLoading() {
        problem.visibility = View.GONE
        loading.visibility = View.VISIBLE
    }

    private fun showWeb() {
        problem.visibility = View.GONE
        web.visibility = View.VISIBLE
    }

    /** The built-in copy of the page. It is the same files the PC serves (copied in when the app is built), so the two never differ. */
    private val localPages = WebViewAssetLoader.Builder()
        .setDomain(LocalPage.HOST)
        .addPathHandler("/") { path ->
            val name = LocalPage.assetFor(path)
            if (name == null) WebResourceResponse("text/plain", "utf-8", 404, "Not found", emptyMap(), ByteArrayInputStream(ByteArray(0)))
            else WebResourceResponse(LocalPage.mimeOf(name), "utf-8", assets.open("web/$name")).apply { responseHeaders = mapOf("Cache-Control" to "no-store") }
        }
        .build()

    @Volatile private var onLocalPage = false  // the built-in page is showing, so there is no PC yet
    @Volatile private var offlineView = false  // the built-in page is showing the saved chats because the PC can't be reached
    @Volatile private var pcPage = false       // the PC's own page is showing (set on the main thread, readable from the page's bridge)
    @Volatile private var deviceName = ""      // what the person called this phone on that page

    // ------------------------------------------------------------------ the PC can't be reached

    private val ui = Handler(Looper.getMainLooper())
    private var attempt = 0                 // each try gets a number, so an answer to an old try is ignored
    private var pageDone = false
    private var shownKind: Diagnosis.Kind? = null  // what the problem panel is saying now, or null
    private var stopWatchingNetwork: (() -> Unit)? = null

    private fun isPrivateOrigin(origin: String?) = origin?.let { Pairing.hostOf(it) }?.let { Pairing.isPrivate(it) } ?: false

    private fun panel(title: String, detail: String, origin: String?, status: String, retry: Boolean, scan: Boolean, other: Boolean, readable: Boolean = false) {
        loading.visibility = if (shownKind == null) View.VISIBLE else View.GONE
        web.visibility = View.INVISIBLE
        findViewById<TextView>(R.id.problem_title).text = title
        findViewById<TextView>(R.id.problem_detail).text = detail
        findViewById<TextView>(R.id.problem_detail).visibility = if (detail.isEmpty()) View.GONE else View.VISIBLE
        findViewById<TextView>(R.id.problem_where).apply {
            text = getString(R.string.problem_where, origin?.substringAfter("://") ?: "")
            visibility = if (origin == null) View.GONE else View.VISIBLE
        }
        findViewById<TextView>(R.id.problem_status).text = status
        findViewById<View>(R.id.retry).visibility = if (retry) View.VISIBLE else View.GONE
        findViewById<View>(R.id.scan_again).visibility = if (scan) View.VISIBLE else View.GONE
        findViewById<View>(R.id.other_pc).visibility = if (other) View.VISIBLE else View.GONE
        findViewById<View>(R.id.saved_chats).visibility = if (readable) View.VISIBLE else View.GONE
        // After a "Switch PC" that went wrong: a way back to the PC it was on before.
        val before = store.previous
        findViewById<View>(R.id.go_back).visibility = if (before != null && before != store.origin) View.VISIBLE else View.GONE
        findViewById<View>(R.id.problem_buttons).visibility = if (retry || scan || other || readable || findViewById<View>(R.id.go_back).visibility == View.VISIBLE) View.VISIBLE else View.GONE
        problem.visibility = View.VISIBLE
    }

    /** Shown at once when the app opens, so it never looks dead while it checks the connection. */
    private fun showConnecting(origin: String) {
        shownKind = null
        panel(getString(R.string.connecting_title), "", origin, "", retry = false, scan = false, other = false)
    }

    /** Says what is wrong in plain words, offers what to do, and keeps trying by itself. */
    private fun showKind(kind: Diagnosis.Kind, origin: String?) {
        shownKind = kind
        val (title, text) = when (kind) {
            Diagnosis.Kind.PHONE_OFFLINE -> R.string.p_phone_offline_title to R.string.p_phone_offline_text
            Diagnosis.Kind.OTHER_NETWORK -> R.string.p_other_network_title to R.string.p_other_network_text
            Diagnosis.Kind.APP_NOT_RUNNING -> R.string.p_app_not_running_title to R.string.p_app_not_running_text
            Diagnosis.Kind.PC_UNREACHABLE -> R.string.p_pc_unreachable_title to R.string.p_pc_unreachable_text
            Diagnosis.Kind.PC_OFFLINE -> R.string.p_pc_offline_title to R.string.p_pc_offline_text
            Diagnosis.Kind.NAME_NOT_FOUND -> R.string.p_name_not_found_title to R.string.p_name_not_found_text
            Diagnosis.Kind.PC_ERROR -> R.string.p_pc_error_title to R.string.p_pc_error_text
            Diagnosis.Kind.ADDRESS_CHANGED -> R.string.p_address_changed_title to R.string.p_address_changed_text
            Diagnosis.Kind.CERTIFICATE -> R.string.problem_certificate to R.string.problem_certificate_hint
        }
        val again = Diagnosis.retries(kind)
        panel(
            getString(title), getString(text), origin.takeIf { kind != Diagnosis.Kind.PHONE_OFFLINE },
            if (again) getString(R.string.problem_retrying) else "",
            retry = again,
            scan = kind != Diagnosis.Kind.PHONE_OFFLINE && kind != Diagnosis.Kind.OTHER_NETWORK && kind != Diagnosis.Kind.APP_NOT_RUNNING,
            other = kind != Diagnosis.Kind.PHONE_OFFLINE && kind != Diagnosis.Kind.APP_NOT_RUNNING,
            readable = origin != null && saved.has(origin),  // the PC can't be reached, but the chats it already sent can still be read
        )
        ui.removeCallbacksAndMessages(null)
        if (again && origin != null) ui.postDelayed({ if (shownKind != null) open(origin, quiet = true) }, 6000)
    }

    /** The PC can't be reached: open the app's own copy of the page on the chats saved from it. Reading only. */
    private fun openSavedChats() {
        val origin = store.origin ?: return
        if (!saved.has(origin)) return
        attempt++
        ui.removeCallbacksAndMessages(null)
        shownKind = null
        blocking = false
        onLocalPage = false
        offlineView = true
        problem.visibility = View.GONE
        loading.visibility = View.VISIBLE
        web.loadUrl(LocalPage.URL + "#/offline")
        ui.postDelayed({ if (offlineView) leaveSavedChatsIfReachable(manual = false) }, 8000)
    }

    /** While saved chats are showing, look for the PC again; when it answers, the real page takes over. `manual` = the person tapped Try again. */
    private fun leaveSavedChatsIfReachable(manual: Boolean) {
        val origin = store.origin ?: return
        val mine = attempt
        if (manual) ui.removeCallbacksAndMessages(null)
        io.execute {
            val net = Reachability.network(this)
            val probe = if (net == Diagnosis.Net.NONE) Diagnosis.Probe.ERROR else Reachability.probe(origin)
            val kind = Diagnosis.diagnose(net, isPrivateOrigin(origin), probe)
            runOnUiThread {
                if (!offlineView || mine != attempt) return@runOnUiThread
                if (kind == null) {
                    offlineView = false
                    load(origin, ++attempt)
                } else {
                    if (manual) web.evaluateJavascript("document.dispatchEvent(new Event('afp-retry-failed'))", null)
                    ui.removeCallbacksAndMessages(null)
                    ui.postDelayed({ if (offlineView) leaveSavedChatsIfReachable(manual = false) }, 8000)
                }
            }
        }
    }

    private fun forgetPc() {
        store.origin?.let { saved.clear(it) }  // a PC that is forgotten takes its saved chats with it
        attempt++
        ui.removeCallbacksAndMessages(null)
        shownKind = null
        store.forget()
        shimInstalledFor = null
        web.loadUrl("about:blank")
        web.clearHistory()
        showWelcome()
    }

    // ------------------------------------------------------------------ what the page may ask the app to do

    inner class AppBridge {
        @JavascriptInterface
        fun scan() = runOnUiThread { if (onLocalPage || isPaired(web.url)) scanner.launch(Intent(this@MainActivity, ScanActivity::class.java)) }

        /** True on the built-in "Connect to your PC" page: there is no PC yet, so that page also asks for its address. */
        @JavascriptInterface
        fun isLocal(): Boolean = onLocalPage

        /** What the person called this phone on that page, used for whichever way they connect. */
        @JavascriptInterface
        fun setName(name: String) { if (onLocalPage) deviceName = name }

        /**
         * The code typed on the built-in page, with an address only if the person had to type one. With no address the PC is the one
         * found on the Wi-Fi. Answers "ok", "none" (no PC found: the page asks for its address), "many" (several found) or "bad".
         */
        @JavascriptInterface
        fun connect(address: String, code: String, name: String): String {
            if (!onLocalPage || Pairing.cleanCode(code).length != 8) return "bad"
            val where = if (address.isNotBlank()) address else {
                val pcs = finder.found()
                if (pcs.isEmpty()) return "none"
                if (pcs.size > 1) return "many"
                pcs[0].origin
            }
            val target = Pairing.fromAddressAndCode(where, code) ?: return "bad"
            deviceName = name
            runOnUiThread { connect(target) }
            return "ok"
        }

        /** "Sign out" in the page: this phone forgets the PC too, and shows the first screen again. */
        @JavascriptInterface
        fun forget() = runOnUiThread { if (isPaired(web.url)) forgetPc() }

        @JavascriptInterface
        fun version(): String = BuildConfig.VERSION_NAME

        /** Whether this phone has a fingerprint or face the page may ask for (for the app lock). */
        @JavascriptInterface
        fun canBiometric(): Boolean = runCatching {
            val bm = getSystemService(BiometricManager::class.java)
            if (Build.VERSION.SDK_INT >= 30) bm.canAuthenticate(BiometricManager.Authenticators.BIOMETRIC_WEAK or BiometricManager.Authenticators.BIOMETRIC_STRONG) == BiometricManager.BIOMETRIC_SUCCESS
            else @Suppress("DEPRECATION") (bm.canAuthenticate() == BiometricManager.BIOMETRIC_SUCCESS)
        }.getOrDefault(false)

        /** Show the system fingerprint/face prompt; the answer goes back to the page as window.__afpBio(true|false). The PIN always remains an alternative. */
        @JavascriptInterface
        fun biometric(title: String) = runOnUiThread {
            val answer = { ok: Boolean -> web.evaluateJavascript("window.__afpBio&&window.__afpBio($ok)", null) }
            runCatching {
                val prompt = BiometricPrompt.Builder(this@MainActivity).setTitle(title.ifBlank { getString(R.string.unlock_app) })
                    .setNegativeButton(getString(R.string.use_pin), mainExecutor) { _, _ -> answer(false) }.build()
                prompt.authenticate(CancellationSignal(), mainExecutor, object : BiometricPrompt.AuthenticationCallback() {
                    override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) = answer(true)
                    override fun onAuthenticationError(errorCode: Int, errString: CharSequence?) = answer(false)
                })
            }.onFailure { answer(false) }
        }

        /** The page shows its "PC unreachable" popup (true) or has closed it (false): while it is up, Back must not get past it. */
        @JavascriptInterface
        fun setBlocking(on: Boolean) { blocking = on }

        /** The popup asks the app to look at the connection; the answer comes back to the page as an "afp-diagnosis" event. */
        @JavascriptInterface
        fun diagnose() = runOnUiThread { if (isPaired(web.url)) diagnoseNow() }

        /** "Switch PC" in the popup: scan another PC's code, keeping this one so a switch that goes wrong can be undone. */
        /** The page's copy of recent chats for the PC this phone is paired with. Only the PC's own page and the built-in saved-chats page may use these. */
        @JavascriptInterface
        fun savedChats(): String = if (pcPage || offlineView) store.origin?.let { saved.read(it) } ?: "" else ""

        @JavascriptInterface
        fun saveChats(json: String) {
            val origin = store.origin
            if (pcPage && origin != null) io.execute { saved.write(origin, json) }  // only the PC's own page makes the copy
        }

        @JavascriptInterface
        fun clearChats() {
            if (pcPage || offlineView) saved.clear(store.origin)
        }

        /** "Try again" on the saved-chats page: the app looks for the PC, and opens its real page if it answers. */
        @JavascriptInterface
        fun retryConnect() = runOnUiThread { if (offlineView) leaveSavedChatsIfReachable(manual = true) }

        @JavascriptInterface
        fun switchPc() = runOnUiThread {
            if (!isPaired(web.url)) return@runOnUiThread
            store.beginSwitch()
            scanner.launch(Intent(this@MainActivity, ScanActivity::class.java))
        }

        @JavascriptInterface
        fun openWifiSettings() = runOnUiThread { runCatching { startActivity(Intent(Settings.ACTION_WIFI_SETTINGS)) } }

        /** The page's haptics, done natively (a web view ignores the page's own vibration until the first tap). */
        @JavascriptInterface
        fun haptic(kind: String) = buzz(kind)

        /** The newest "your PC is going to sleep / shutting down" notice, as "kind|time", or "". */
        @JavascriptInterface
        fun lastNotice(): String = store.notice ?: ""

        /** Notifications on: ask Android for permission, get this phone's Firebase address, and tell the page (an "afp-push" event). */
        @JavascriptInterface
        fun enablePush() = runOnUiThread {
            if (!isPaired(web.url)) return@runOnUiThread
            withNotifications { allowed ->
                if (!allowed) web.evaluateJavascript(PushLink.tokenScript(null, getString(R.string.push_denied)), null)
                else Push.token(this@MainActivity) { token, why -> runOnUiThread { web.evaluateJavascript(PushLink.tokenScript(token, why), null) } }
            }
        }

        /** Notifications off: the old address stops working. */
        @JavascriptInterface
        fun disablePush() = runOnUiThread { if (isPaired(web.url)) Push.forget(this@MainActivity) }

        // ---- big downloads: kept as a partial file inside the app while they arrive (so they can be resumed, even after the app was closed),
        // then copied into Downloads when complete. The page asks for them by a key.

        @JavascriptInterface
        fun partSize(key: String): Long = if (pcPage) partFile(key).let { if (it.exists()) it.length() else 0L } else 0L

        /** Write the next piece at `offset`. Refuses when the file is not exactly that long, so a lost or repeated piece can never corrupt it. */
        @JavascriptInterface
        fun appendPart(key: String, base64: String, offset: Long): Boolean {
            if (!pcPage) return false
            return runCatching {
                val file = partFile(key)
                java.io.RandomAccessFile(file, "rw").use { raf ->
                    if (raf.length() < offset) return false
                    raf.setLength(offset)  // a piece that half arrived before a drop is replaced
                    raf.seek(offset)
                    raf.write(Base64.decode(base64, Base64.DEFAULT))
                }
                true
            }.getOrDefault(false)
        }

        @JavascriptInterface
        fun discardPart(key: String) { if (pcPage) runCatching { partFile(key).delete() } }

        /** Keep only the partial files the page still lists; anything else is leftovers. */
        @JavascriptInterface
        fun dropPartsExcept(keysJson: String) {
            if (!pcPage) return
            runCatching {
                val keep = JSONArray(keysJson).let { a -> (0 until a.length()).map { safeKey(a.getString(it)) }.toSet() }
                partsDir().listFiles()?.forEach { if (it.name.removeSuffix(".part") !in keep) it.delete() }
            }
        }

        @JavascriptInterface
        fun freeBytes(): Long = runCatching { partsDir().usableSpace }.getOrDefault(0L)

        /** The partial file is complete: copy it into Downloads. Answers through window.__afpSaved(key, ok, uri). */
        @JavascriptInterface
        fun finishPart(key: String, name: String, mime: String) {
            if (!pcPage) return
            io.execute {
                var saved = ""
                val ok = runCatching {
                    val clean = name.replace(Regex("[\\\\/:*?\"<>|]"), "_").ifBlank { "download" }
                    val values = ContentValues().apply {
                        put(MediaStore.Downloads.DISPLAY_NAME, clean)
                        put(MediaStore.Downloads.MIME_TYPE, mime)
                        put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
                        put(MediaStore.Downloads.IS_PENDING, 1)
                    }
                    val uri = contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values) ?: error("no uri")
                    try {
                        contentResolver.openOutputStream(uri)!!.use { out -> partFile(key).inputStream().use { it.copyTo(out, 256 * 1024) } }
                        contentResolver.update(uri, ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }, null, null)
                    } catch (e: Exception) { runCatching { contentResolver.delete(uri, null, null) }; throw e }
                    partFile(key).delete()
                    saved = uri.toString()
                    true
                }.getOrDefault(false)
                runOnUiThread { web.evaluateJavascript("window.__afpSaved&&window.__afpSaved(${JSONObject.quote(key)},$ok,${JSONObject.quote(saved)})", null) }
            }
        }

        @JavascriptInterface
        fun deleteSaved(uri: String): Boolean = pcPage && runCatching { contentResolver.delete(Uri.parse(uri), null, null) > 0 }.getOrDefault(false)

        @JavascriptInterface
        fun savedExists(uri: String): Boolean = runCatching { contentResolver.openFileDescriptor(Uri.parse(uri), "r")?.use { true } ?: false }.getOrDefault(false)

        @JavascriptInterface
        fun openSaved(uri: String, mime: String): Boolean {
            if (!pcPage) return false
            return runCatching {
                startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(Uri.parse(uri), mime.ifBlank { "*/*" }).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK))
                true
            }.getOrDefault(false)
        }

        /** While a download runs: hold the app up (a notification shows the progress), and don't let the page's timers sleep. */
        @JavascriptInterface
        fun keepAlive(title: String, text: String, percent: Int) {
            if (!pcPage) return
            keepRunning = true
            runCatching { DownloadService.show(this@MainActivity, title, text, percent) }
        }

        @JavascriptInterface
        fun keepAliveOff() {
            keepRunning = false
            runCatching { DownloadService.hide(this@MainActivity) }
        }

        @JavascriptInterface
        fun saveFile(name: String, mime: String, base64: String) {
            if (!pcPage) return
            io.execute {
                val ok = runCatching { saveToDownloads(name, mime, Base64.decode(base64, Base64.DEFAULT)) }.getOrDefault(false)
                runOnUiThread {
                    Toast.makeText(this@MainActivity, if (ok) getString(R.string.saved, name) else getString(R.string.save_failed), Toast.LENGTH_LONG).show()
                }
            }
        }
    }

    @Volatile private var keepRunning = false  // a download is running: the page must keep working when the app is not on screen
    private fun safeKey(key: String) = key.replace(Regex("[^A-Za-z0-9_-]"), "_").take(80)
    private fun partsDir() = File(filesDir, "dl").apply { mkdirs() }
    private fun partFile(key: String) = File(partsDir(), safeKey(key) + ".part")

    private fun saveToDownloads(name: String, mime: String, bytes: ByteArray): Boolean {
        val clean = name.replace(Regex("[\\\\/:*?\"<>|]"), "_").ifBlank { "download" }
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, clean)
            put(MediaStore.Downloads.MIME_TYPE, mime)
            put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
            put(MediaStore.Downloads.IS_PENDING, 1)
        }
        val uri = contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values) ?: return false
        contentResolver.openOutputStream(uri)?.use { it.write(bytes) } ?: return false
        contentResolver.update(uri, ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }, null, null)
        return true
    }

    override fun onResume() {
        super.onResume()
        current = this
        visible = true
        web.onResume()
        // Coming back to the app while it says the PC is down: check again now, don't wait for the next automatic try.
        val kind = shownKind
        val origin = store.origin
        if (offlineView) leaveSavedChatsIfReachable(manual = false)
        else if (kind != null && origin != null && Diagnosis.retries(kind)) open(origin, quiet = true)
    }

    override fun onPause() {
        visible = false
        if (!keepRunning) web.onPause()
        super.onPause()
    }

    override fun onDestroy() {
        if (current === this) current = null
        speech.release()
        tts.release()
        finder.stop()
        stopWatchingNetwork?.invoke()
        ui.removeCallbacksAndMessages(null)
        io.shutdown()
        web.destroy()
        super.onDestroy()
    }

    companion object {
        /** The open screen, and whether it is in front: a notification is shown only when the app is not. */
        @Volatile var current: MainActivity? = null
        @Volatile var visible = false
    }
}
