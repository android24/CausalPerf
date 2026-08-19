package dev.causalperf.startup.cpu

import android.app.Activity
import android.os.Bundle
import android.widget.LinearLayout
import android.widget.TextView

class MainActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val app = application as CpuBenchmarkApplication

        val title = TextView(this).apply {
            id = R.id.title
            text = "Lookup table ready"
            textSize = 24f
        }
        val digest = TextView(this).apply {
            id = R.id.digest
            text = app.startupDigest.toString()
            textSize = 16f
        }
        val samples = TextView(this).apply {
            id = R.id.samples
            text = app.startupTable.take(8).joinToString(",")
            textSize = 14f
        }

        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 72, 48, 48)
            addView(title)
            addView(digest)
            addView(samples)
        })
    }
}

