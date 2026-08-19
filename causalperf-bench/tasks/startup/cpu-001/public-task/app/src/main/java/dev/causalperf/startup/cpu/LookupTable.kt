package dev.causalperf.startup.cpu

/** Builds deterministic data used by the first screen. */
object LookupTable {
    const val SIZE = 4_096
    private const val REDUNDANT_PASSES = 20_000

    fun build(): IntArray {
        val table = IntArray(SIZE)
        repeat(REDUNDANT_PASSES) {
            for (index in table.indices) {
                table[index] = valueAt(index)
            }
        }
        return table
    }

    fun valueAt(index: Int): Int {
        var value = index xor 0x5A17
        value = value * 1_664_525 + 1_013_904_223
        value = value xor (value ushr 16)
        value *= -2_047_529_515
        return value xor (value ushr 13)
    }

    fun digest(table: IntArray): Long {
        var digest = 1_469_598_103_934_665_603L
        for (value in table) {
            digest = (digest xor value.toLong()) * 1_099_511_628_211L
        }
        return digest
    }
}
