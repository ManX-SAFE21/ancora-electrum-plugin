package life.after.bitcoin

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import life.after.bitcoin.databinding.ActivityResultBinding
import org.json.JSONException
import org.json.JSONObject

class ResultActivity : AppCompatActivity() {

    private lateinit var binding: ActivityResultBinding
    private var kind = "txs"
    private var payload = ""
    private var parts: List<String> = emptyList()

    private val saveWillPicker =
        registerForActivityResult(ActivityResultContracts.CreateDocument("application/json")) { uri: Uri? ->
            saveTo(uri)
        }
    private val saveTxsPicker =
        registerForActivityResult(ActivityResultContracts.CreateDocument("text/plain")) { uri: Uri? ->
            saveTo(uri)
        }

    private fun saveTo(uri: Uri?) {
        if (uri != null) {
            contentResolver.openOutputStream(uri)?.use { it.write(payload.toByteArray()) }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityResultBinding.inflate(layoutInflater)
        setContentView(binding.root)

        kind = intent.getStringExtra(EXTRA_KIND) ?: "txs"
        payload = intent.getStringExtra(EXTRA_PAYLOAD) ?: ""
        parts = intent.getStringArrayListExtra(EXTRA_PARTS) ?: emptyList()

        binding.tvKind.text =
            getString(if (kind == "will") R.string.result_kind_will else R.string.result_kind_txs)
        binding.tvContent.text = pretty(payload)

        binding.btnCopy.setOnClickListener {
            val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            clipboard.setPrimaryClip(ClipData.newPlainText("BAL transfer", payload))
            Toast.makeText(this, R.string.copied_toast, Toast.LENGTH_SHORT).show()
        }
        binding.btnShare.setOnClickListener {
            val send = Intent(Intent.ACTION_SEND).apply {
                type = "text/plain"
                putExtra(Intent.EXTRA_TEXT, payload)
            }
            startActivity(Intent.createChooser(send, null))
        }
        binding.btnSave.setOnClickListener { saveFile() }
        binding.btnScanAnother.setOnClickListener { finish() }
    }

    private fun pretty(json: String): String {
        if (kind == "will") {
            try {
                return JSONObject(json).toString(2)
            } catch (_: JSONException) {
                return json
            }
        }
        // Transaction list: one numbered line per tx.
        if (parts.isNotEmpty()) {
            return parts.mapIndexed { i, tx -> "%d. %s".format(i + 1, tx) }
                .joinToString("\n")
        }
        return json
    }

    private fun saveFile() {
        val name = if (kind == "will") {
            getString(R.string.save_file_will)
        } else {
            getString(R.string.save_file_txs)
        }
        // ActivityResultContracts.CreateDocument takes the suggested file name;
        // it maps it to ACTION_CREATE_DOCUMENT + EXTRA_TITLE internally.
        val picker = if (kind == "will") saveWillPicker else saveTxsPicker
        picker.launch(name)
    }

    companion object {
        const val EXTRA_KIND = "kind"
        const val EXTRA_PAYLOAD = "payload"
        const val EXTRA_PARTS = "parts"
    }
}