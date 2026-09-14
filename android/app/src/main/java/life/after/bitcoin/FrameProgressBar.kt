package life.after.bitcoin

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View
import androidx.core.content.ContextCompat

/**
 * Horizontal progress bar showing how many QR frames of the current transfer
 * have been captured (`received / total`), with a filled mint segment
 * proportional to the fraction. A thin decorative strip over the camera
 * preview; the exact count stays in the header's "n / N" label.
 */
class FrameProgressBar @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {

    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = ContextCompat.getColor(context, R.color.frame_fill)
    }
    private val trackPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = ContextCompat.getColor(context, R.color.frame_track)
    }
    private val trackRect = RectF()
    private val fillRect = RectF()
    private val cornerRadius = dp(3f)

    private var fraction = 0f

    /** Reset to an empty bar. */
    fun reset() {
        fraction = 0f
        invalidate()
    }

    /**
     * Update the fill to [received] out of [total] frames captured.
     * A zero/unknown total clears the bar.
     */
    fun set(total: Int, received: Int) {
        fraction = if (total > 0) {
            received.toFloat() / total.toFloat()
        } else {
            0f
        }
        invalidate()
    }

    private fun dp(value: Float): Float =
        resources.displayMetrics.density * value

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        if (width <= 0 || height <= 0) {
            return
        }
        trackRect.set(0f, 0f, width.toFloat(), height.toFloat())
        canvas.drawRoundRect(trackRect, cornerRadius, cornerRadius, trackPaint)
        if (fraction <= 0f) {
            return
        }
        val fillWidth = width * fraction.coerceIn(0f, 1f)
        fillRect.set(0f, 0f, fillWidth, height.toFloat())
        canvas.drawRoundRect(fillRect, cornerRadius, cornerRadius, fillPaint)
    }
}