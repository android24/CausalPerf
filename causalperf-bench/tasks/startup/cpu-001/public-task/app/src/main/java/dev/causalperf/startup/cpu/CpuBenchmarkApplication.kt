package dev.causalperf.startup.cpu

import android.app.Application
import android.os.Trace

class CpuBenchmarkApplication : Application() {
    lateinit var startupTable: IntArray
        private set

    var startupDigest: Long = 0
        private set

    override fun onCreate() {
        super.onCreate()
        Trace.beginSection("CausalPerf#BuildLookupTable")
        try {
            startupTable = LookupTable.build()
            startupDigest = LookupTable.digest(startupTable)
        } finally {
            Trace.endSection()
        }
    }
}

