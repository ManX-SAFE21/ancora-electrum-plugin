package life.after.bitcoin

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.util.Log
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.BarcodeScanner
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import life.after.bitcoin.BalDecoder.AddResult
import life.after.bitcoin.databinding.ActivityMainBinding
import java.util.concurrent.Executors

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var decoder: BalDecoder
    private lateinit var barcodeScanner: BarcodeScanner

    private val analyzerExecutor = Executors.newSingleThreadExecutor()
    private var finished = false
    private var cameraBound = false
    private var lastAnalysisMs = 0L

    private val formatLabels: Map<String, String> by lazy {
        mapOf(
            "balqr" to getString(R.string.format_balqr),
            "ur1" to getString(R.string.format_ur1),
            "ur2" to getString(R.string.format_ur2),
            "bbqr" to getString(R.string.format_bbqr),
        )
    }

    private val requestCameraPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) {
                startCamera()
            } else {
                Toast.makeText(this, R.string.permission_denied, Toast.LENGTH_LONG).show()
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        decoder = BalDecoder(applicationContext)
        barcodeScanner = BarcodeScanning.getClient(
            BarcodeScannerOptions.Builder()
                .setBarcodeFormats(Barcode.FORMAT_QR_CODE)
                .build()
        )

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
            == PackageManager.PERMISSION_GRANTED
        ) {
            startCamera()
        } else {
            requestCameraPermission.launch(Manifest.permission.CAMERA)
        }
    }

    override fun onResume() {
        super.onResume()
        // Returning from the result screen starts a new scan.
        if (finished) {
            finished = false
            decoder.reset()
            binding.tvFormat.text = getString(R.string.format_placeholder)
            binding.tvProgress.text = "0 / 0"
            binding.tvStatus.setText(R.string.status_waiting)
            binding.frameBar.reset()
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
            == PackageManager.PERMISSION_GRANTED
        ) {
            startCamera()
        }
    }

    override fun onDestroy() {
        cameraProvider?.unbindAll()
        barcodeScanner.close()
        analyzerExecutor.shutdown()
        super.onDestroy()
    }

    private var cameraProvider: ProcessCameraProvider? = null

    private fun startCamera() {
        if (cameraBound) {
            return
        }
        val providerFuture = ProcessCameraProvider.getInstance(this)
        providerFuture.addListener({
            val provider = providerFuture.get()
            cameraProvider = provider

            val preview = Preview.Builder().build()
            preview.setSurfaceProvider(binding.previewView.surfaceProvider)
            val analysis = ImageAnalysis.Builder()
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .build()
            analysis.setAnalyzer(analyzerExecutor) { proxy -> analyze(proxy) }

            try {
                provider.unbindAll()
                provider.bindToLifecycle(
                    this, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis
                )
                cameraBound = true
            } catch (e: Exception) {
                Log.e(TAG, "Failed to bind camera", e)
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun analyze(proxy: ImageProxy) {
        val now = System.currentTimeMillis()
        if (finished || now - lastAnalysisMs < 100) {
            proxy.close()
            return
        }
        lastAnalysisMs = now
        val image = proxy.image
        if (image == null) {
            proxy.close()
            return
        }
        try {
            val input = InputImage.fromMediaImage(image, proxy.imageInfo.rotationDegrees)
            barcodeScanner.process(input)
                .addOnSuccessListener { barcodes ->
                    for (barcode in barcodes) {
                        val value = barcode.rawValue
                        if (!value.isNullOrEmpty()) {
                            handleFrame(value)
                            break
                        }
                    }
                }
                .addOnCompleteListener { proxy.close() }
        } catch (e: Exception) {
            Log.w(TAG, "Frame analysis failure", e)
            proxy.close()
        }
    }

    private fun handleFrame(value: String) {
        if (finished) {
            return
        }
        when (decoder.add(value)) {
            AddResult.OK -> {
                Log.i(TAG, "frame ok fmt=${decoder.format} rcvd=${decoder.received}/${decoder.total} done=${decoder.done}")
                binding.tvFormat.text = decoder.format?.let { formatLabels[it] }
                    ?: getString(R.string.format_placeholder)
                binding.tvProgress.text =
                    getString(R.string.progress_fmt, decoder.received, decoder.total)
                binding.frameBar.set(decoder.total, decoder.received)
                if (decoder.done) {
                    finishScan()
                }
            }
            AddResult.DUP -> Log.i(TAG, "frame dup")
            AddResult.GARBAGE -> Log.w(TAG, "frame garbage")
            AddResult.CONFLICT -> {
                Log.w(TAG, "format conflict - resetting")
                decoder.reset()
                binding.tvFormat.text = getString(R.string.format_placeholder)
                binding.tvProgress.text = "0 / 0"
                binding.tvStatus.setText(R.string.conflict_message)
                binding.frameBar.reset()
            }
        }
    }

    private fun finishScan() {
        if (finished) {
            return
        }
        finished = true
        val result = decoder.finish()
        Log.i(TAG, "FINISH kind=${result.kind} payload=${result.payload.length}B parts=${result.parts.size}")
        val intent = Intent(this, ResultActivity::class.java).apply {
            putExtra(ResultActivity.EXTRA_KIND, result.kind)
            putExtra(ResultActivity.EXTRA_PAYLOAD, result.payload)
            putStringArrayListExtra(ResultActivity.EXTRA_PARTS, ArrayList(result.parts))
        }
        startActivity(intent)
    }

    companion object {
        private const val TAG = "BalReader"
    }
}