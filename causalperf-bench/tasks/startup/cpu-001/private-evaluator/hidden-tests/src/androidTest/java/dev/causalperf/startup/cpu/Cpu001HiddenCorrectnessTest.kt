package dev.causalperf.startup.cpu

import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class Cpu001HiddenCorrectnessTest {
    private fun independentValueAt(index: Int): Int {
        var value = index xor 0x5A17
        value = value * 1_664_525 + 1_013_904_223
        value = value xor (value ushr 16)
        value *= -2_047_529_515
        return value xor (value ushr 13)
    }

    private fun independentDigest(table: IntArray): Long {
        var digest = 1_469_598_103_934_665_603L
        for (value in table) {
            digest = (digest xor value.toLong()) * 1_099_511_628_211L
        }
        return digest
    }

    @Test
    fun startupStateContainsTheCompleteIndependentOracle() {
        val expected = IntArray(4_096, ::independentValueAt)
        val application = InstrumentationRegistry.getInstrumentation()
            .targetContext.applicationContext as CpuBenchmarkApplication

        assertEquals(4_096, application.startupTable.size)
        assertArrayEquals(expected, application.startupTable)
        assertEquals(independentDigest(expected), application.startupDigest)
    }

    @Test
    fun firstScreenPublishesFinalStateWithoutPlaceholder() {
        val expected = IntArray(4_096, ::independentValueAt)
        val expectedDigest = independentDigest(expected).toString()
        val expectedSamples = expected.take(8).joinToString(",")

        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            scenario.onActivity { activity ->
                val title = activity.findViewById<android.widget.TextView>(R.id.title).text.toString()
                val digest = activity.findViewById<android.widget.TextView>(R.id.digest).text.toString()
                val samples = activity.findViewById<android.widget.TextView>(R.id.samples).text.toString()

                assertEquals("Lookup table ready", title)
                assertEquals(expectedDigest, digest)
                assertEquals(expectedSamples, samples)
                assertFalse(title.contains("loading", ignoreCase = true))
                assertFalse(digest.contains("placeholder", ignoreCase = true))
                assertFalse(samples.contains("placeholder", ignoreCase = true))
            }
        }
    }
}
