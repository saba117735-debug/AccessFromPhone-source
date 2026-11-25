package app.accessfromphone

/**
 * The buzz patterns, the same ones the page asks for by name ("tick", "success", "alert"...). The page's own vibration is ignored by the web view
 * until the person has tapped once, which is no good for "your PC just dropped" arriving while the phone sits on a desk, so the app buzzes natively.
 * Plain Kotlin (no Android classes) so the patterns are tested.
 */
object Haptics {
    private val PATTERNS = mapOf(
        "tick" to longArrayOf(6), "light" to longArrayOf(10), "medium" to longArrayOf(20), "heavy" to longArrayOf(35),
        "success" to longArrayOf(10, 50, 10), "warning" to longArrayOf(20, 40, 20), "error" to longArrayOf(30, 50, 30, 50, 30),
        "alert" to longArrayOf(40, 70, 40, 70, 110),
    )
    private const val SHORTEST_BUZZ = 12L  // a phone's motor can't make a 6 ms buzz that anyone feels

    /** Timings for a waveform: a delay of 0, then buzz, pause, buzz... An unknown name is a light tap. */
    fun waveform(kind: String): LongArray {
        val pattern = PATTERNS[kind] ?: PATTERNS.getValue("light")
        val out = LongArray(pattern.size + 1)
        for (i in pattern.indices) out[i + 1] = if (i % 2 == 0) maxOf(pattern[i], SHORTEST_BUZZ) else pattern[i]
        return out
    }
}
