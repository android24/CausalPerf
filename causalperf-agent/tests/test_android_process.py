from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from causalperf_agent.android import ProcessSpec, SubprocessTransport


class SubprocessTransportTest(unittest.TestCase):
    def spec(self, **overrides):
        value = {
            "argv": ("/trusted/gradlew", "clean", ":app:assembleBenchmark"),
            "working_directory": Path("/trusted/task"),
            "environment": {"JAVA_HOME": "/trusted/jdk"},
            "timeout_seconds": 12,
            "output_limit_bytes": 8,
        }
        value.update(overrides)
        return ProcessSpec(**value)

    @patch("causalperf_agent.android.process.subprocess.run")
    def test_uses_exact_argv_environment_and_never_a_shell(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, b"ok", b"")
        output = SubprocessTransport().run(self.spec())
        self.assertEqual(output.returncode, 0)
        run.assert_called_once_with(
            ["/trusted/gradlew", "clean", ":app:assembleBenchmark"],
            cwd=Path("/trusted/task"), env={"JAVA_HOME": "/trusted/jdk"},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=12,
            check=False, shell=False,
        )

    @patch("causalperf_agent.android.process.subprocess.run")
    def test_output_is_bounded_and_truncation_is_explicit(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, b"123456", b"abcdef")
        output = SubprocessTransport().run(self.spec())
        self.assertEqual(output.stdout, b"123456")
        self.assertEqual(output.stderr, b"ab")
        self.assertTrue(output.output_truncated)

    @patch("causalperf_agent.android.process.subprocess.run")
    def test_timeout_is_fail_closed_with_bounded_partial_output(self, run):
        run.side_effect = subprocess.TimeoutExpired(
            cmd=["/trusted/gradlew"], timeout=12, output=b"123456", stderr=b"abcdef"
        )
        output = SubprocessTransport().run(self.spec())
        self.assertIsNone(output.returncode)
        self.assertTrue(output.timed_out)
        self.assertTrue(output.output_truncated)
        self.assertEqual(len(output.stdout) + len(output.stderr), 8)


if __name__ == "__main__":
    unittest.main()
