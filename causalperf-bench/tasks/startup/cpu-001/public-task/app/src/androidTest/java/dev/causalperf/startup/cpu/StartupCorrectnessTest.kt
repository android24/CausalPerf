package dev.causalperf.startup.cpu

import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class StartupCorrectnessTest {
    @Test
    fun firstScreenRepresentsTheCompleteLookupTable() {
        val expected = IntArray(LookupTable.SIZE) { LookupTable.valueAt(it) }
        val expectedDigest = LookupTable.digest(expected)
        val expectedSamples = expected.take(8).joinToString(",")

        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            scenario.onActivity { activity ->
                val app = activity.application as CpuBenchmarkApplication
                assertEquals(LookupTable.SIZE, app.startupTable.size)
                assertEquals(expectedDigest, app.startupDigest)
                assertEquals(expected.asList(), app.startupTable.asList())
                assertEquals(
                    expectedDigest.toString(),
                    activity.findViewById<android.widget.TextView>(R.id.digest).text.toString(),
                )
                assertEquals(
                    expectedSamples,
                    activity.findViewById<android.widget.TextView>(R.id.samples).text.toString(),
                )
                assertNotEquals(0L, app.startupDigest)
            }
        }
    }
}

