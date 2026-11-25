package app.accessfromphone

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.VibrationEffect
import android.os.Vibrator
import android.view.View
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import com.google.zxing.BarcodeFormat
import com.journeyapps.barcodescanner.BarcodeCallback
import com.journeyapps.barcodescanner.BarcodeResult
import com.journeyapps.barcodescanner.DecoratedBarcodeView
import com.journeyapps.barcodescanner.DefaultDecoderFactory

/** Reads the QR code the PC shows. Only a code that is really from the PC (a link with a pairing code) is accepted. */
class ScanActivity : AppCompatActivity() {
    private lateinit var view: DecoratedBarcodeView
    private var torch = false

    private val camera = registerForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        if (ok) startScanning() else {
            findViewById<TextView>(R.id.hint).setText(R.string.camera_needed)
            findViewById<View>(R.id.grant).visibility = View.VISIBLE
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_scan)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        WindowCompat.getInsetsController(window, findViewById(R.id.scan_root)).isAppearanceLightStatusBars = false
        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.chrome)) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            WindowInsetsCompat.CONSUMED
        }
        view = findViewById(R.id.scanner)
        view.barcodeView.decoderFactory = DefaultDecoderFactory(listOf(BarcodeFormat.QR_CODE))
        view.setStatusText("")
        findViewById<View>(R.id.close).setOnClickListener { finish() }
        findViewById<View>(R.id.grant).setOnClickListener { camera.launch(Manifest.permission.CAMERA) }
        findViewById<View>(R.id.torch).setOnClickListener {
            torch = !torch
            if (torch) view.setTorchOn() else view.setTorchOff()
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) startScanning()
        else camera.launch(Manifest.permission.CAMERA)
    }

    private fun startScanning() {
        findViewById<View>(R.id.grant).visibility = View.GONE
        findViewById<TextView>(R.id.hint).setText(R.string.scan_hint)
        view.resume()
        listen()
    }

    private fun listen() {
        view.decodeSingle(object : BarcodeCallback {
            override fun barcodeResult(result: BarcodeResult) {
                val target = Pairing.fromQr(result.text.orEmpty())
                if (target == null) {  // a QR code, but not the PC's: say so and keep looking
                    Toast.makeText(this@ScanActivity, R.string.scan_wrong, Toast.LENGTH_SHORT).show()
                    view.postDelayed({ listen() }, 1400)
                    return
                }
                buzz()
                setResult(RESULT_OK, Intent().putExtra(RESULT_URL, target.url))
                finish()
            }
        })
    }

    @Suppress("DEPRECATION")
    private fun buzz() {
        val vibrator = getSystemService(Vibrator::class.java) ?: return
        vibrator.vibrate(VibrationEffect.createOneShot(40, VibrationEffect.DEFAULT_AMPLITUDE))
    }

    override fun onResume() {
        super.onResume()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) view.resume()
    }

    override fun onPause() {
        view.pause()
        super.onPause()
    }

    companion object {
        const val RESULT_URL = "url"
    }
}
