package life.after.bitcoin

import android.content.Context
import com.chaquo.python.PyObject
import com.chaquo.python.PyException
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONException
import org.json.JSONObject

/**
 * Chaquopy bridge over the plugin's animated-QR codecs
 * (``bal.core.animated_qr``, ``bal.core.qrtransfer``, bundled verbatim under
 * ``app/src/main/python``).
 *
 * The decode tail mirrors the plugin's import dialog exactly, and runs
 * entirely inside Python (``balreader.bridge.finish``) so no container
 * conversion happens across the bridge:
 *
 *   session.resolve() -> qrtransfer.decode_transfer() -> decode_will_payload()
 *
 * Kotlin only feeds frames, reads progress, and renders the JSON the bridge
 * returns.
 */
class BalDecoder(private val context: Context) {

    /** Outcome of feeding one scanned frame to the session. */
    enum class AddResult {
        /** A new frame was accepted. */
        OK,

        /** The frame was already present (duplicate); ignore. */
        DUP,

        /** The frame was not a supported QR transfer; ignore. */
        GARBAGE,

        /** The QR switched to a different transfer; caller should rescan. */
        CONFLICT,
    }

    /** Fully decoded transfer, mirroring the plugin's import tail. */
    data class DecodedResult(
        val kind: String,       // "will", "txs" or "error"
        val payload: String,    // raw transfer text (JSON or joined tx hexes)
        val parts: List<String> // [whole-will JSON] or [tx hex strings]
    )

    private val python: Python by lazy {
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(context))
        }
        Python.getInstance()
    }
    private val animatedQr by lazy { python.getModule("bal.core.animated_qr") }
    private val bridge by lazy { python.getModule("balreader.bridge") }

    private var session: PyObject? = null

    /** Start a fresh receive session (clears any accumulated frames). */
    fun reset() {
        session = null
    }

    private fun sessionOrCreate(): PyObject {
        val current = session
        if (current != null) {
            return current
        }
        return animatedQr.callAttr("AnimatedQrSession").also { session = it }
    }

    /** Feed one scanned frame string; see [AddResult] for semantics. */
    fun add(text: String): AddResult {
        return try {
            when (sessionOrCreate().callAttr("add_part", text).toString()) {
                "dup" -> AddResult.DUP
                else -> AddResult.OK
            }
        } catch (e: PyException) {
            val msg = e.message ?: ""
            // TransferConflictError: "Switched QR format mid-import (.. -> ..)".
            if (msg.contains("Switched QR format")) {
                AddResult.CONFLICT
            } else {
                AddResult.GARBAGE
            }
        }
    }

    /** The detected wire format ("balqr"/"ur1"/"ur2"/"bbqr"), or null. */
    val format: String?
        get() = runCatching {
            session?.get("format")?.toString()?.takeIf { it != "None" }
        }.getOrNull()

    /** Number of distinct frames accepted. */
    val received: Int
        get() = session?.get("received")?.toInt() ?: 0

    /** Total frames expected for the current transfer (0 until known). */
    val total: Int
        get() = session?.get("total")?.toInt() ?: 0

    /** True once the whole transfer has been captured. */
    val done: Boolean
        get() = session?.get("done")?.toBoolean() ?: false

    /**
     * Resolve the completed session into a [DecodedResult]. The decoding runs
     * in Python (``balreader.bridge.finish``) using the exact same three steps
     * as the plugin's import dialog.
     */
    fun finish(): DecodedResult {
        val jsonText = bridge.callAttr("finish", sessionOrCreate()).toString()
        return try {
            val obj = JSONObject(jsonText)
            val partsArray = obj.getJSONArray("parts")
            val parts = (0 until partsArray.length()).map { partsArray.getString(it) }
            DecodedResult(
                kind = obj.getString("kind"),
                payload = obj.getString("payload"),
                parts = parts,
            )
        } catch (e: JSONException) {
            DecodedResult(kind = "error", payload = jsonText, parts = emptyList())
        }
    }
}