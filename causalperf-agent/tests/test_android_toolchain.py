import tempfile
import unittest
from pathlib import Path

from causalperf_agent.android import (
    ToolchainConfigError,
    ToolchainProfile,
    load_toolchain_profile,
    resolve_toolchain,
)


REPO_ROOT = Path(__file__).parents[2]


class AndroidToolchainConfigTest(unittest.TestCase):
    def write_config(self, value):
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "toolchains.toml"
        path.write_text(value, encoding="utf-8")
        self.addCleanup(directory.cleanup)
        return path

    def test_profile_is_selected_by_current_host(self):
        path = self.write_config(
            """
schema_version = 1
[profiles.mac]
host_os = "Darwin"
jdk_home = "/jdk/mac"
[profiles.linux]
host_os = "Linux"
jdk_home = "/jdk/linux"
"""
        )

        profile = load_toolchain_profile(path, host_os="Linux")

        self.assertEqual(profile.name, "linux")
        self.assertEqual(profile.jdk_home, "/jdk/linux")

    def test_repository_example_has_one_valid_profile_per_supported_host(self):
        path = REPO_ROOT / "config" / "android-toolchains.example.toml"

        for host in ("Darwin", "Windows", "Linux"):
            with self.subTest(host=host):
                profile = load_toolchain_profile(path, host_os=host)
                self.assertEqual(profile.host_os, host)

    def test_windows_roots_derive_native_executable_paths(self):
        profile = ToolchainProfile(
            name="windows",
            host_os="Windows",
            jdk_home=r"C:\Java\jdk-17",
            android_sdk_root=r"D:\Android\Sdk",
            gradle_home=r"E:\Gradle\gradle-9.5.0",
        )

        resolved = resolve_toolchain(profile, environment={}, host_os="Windows")

        self.assertEqual(resolved.tool_paths["java"], r"C:\Java\jdk-17\bin\java.exe")
        self.assertEqual(resolved.tool_paths["adb"], r"D:\Android\Sdk\platform-tools\adb.exe")
        self.assertEqual(
            resolved.tool_paths["gradle"],
            r"E:\Gradle\gradle-9.5.0\bin\gradle.bat",
        )
        self.assertEqual(resolved.environment["ANDROID_SDK_ROOT"], r"D:\Android\Sdk")

    def test_cli_roots_override_profile_executables(self):
        profile = ToolchainProfile(
            name="linux",
            host_os="Linux",
            jdk_home="/config/jdk",
            android_sdk_root="/config/sdk",
            java_executable="/config/special-java",
            adb_executable="/config/special-adb",
            gradle_executable="./config-gradlew",
        )

        resolved = resolve_toolchain(
            profile,
            environment={},
            host_os="Linux",
            overrides={
                "jdk_home": "/cli/jdk",
                "android_sdk_root": "/cli/sdk",
                "gradle_executable": "./cli-gradlew",
            },
        )

        self.assertEqual(resolved.tool_paths["java"], "/cli/jdk/bin/java")
        self.assertEqual(resolved.tool_paths["adb"], "/cli/sdk/platform-tools/adb")
        self.assertEqual(resolved.tool_paths["gradle"], "./cli-gradlew")
        self.assertEqual(resolved.environment["JAVA_HOME"], "/cli/jdk")

    def test_environment_roots_are_used_without_a_config_file(self):
        resolved = resolve_toolchain(
            environment={
                "JAVA_HOME": "/env/jdk",
                "ANDROID_HOME": "/env/sdk",
                "GRADLE_HOME": "/env/gradle",
            },
            host_os="Linux",
        )

        self.assertEqual(resolved.profile_name, "ambient")
        self.assertEqual(resolved.tool_paths["java"], "/env/jdk/bin/java")
        self.assertEqual(resolved.tool_paths["adb"], "/env/sdk/platform-tools/adb")
        self.assertEqual(resolved.tool_paths["gradle"], "/env/gradle/bin/gradle")

    def test_profile_for_another_operating_system_is_rejected(self):
        path = self.write_config(
            r"""
schema_version = 1
[profiles.windows]
host_os = "Windows"
jdk_home = 'C:\Java\jdk-17'
"""
        )

        with self.assertRaisesRegex(ToolchainConfigError, "current host is Linux"):
            load_toolchain_profile(path, profile_name="windows", host_os="Linux")

    def test_unknown_profile_field_fails_closed(self):
        path = self.write_config(
            """
schema_version = 1
[profiles.linux]
host_os = "Linux"
jdk_home = "/jdk"
download_sdk_automatically = "true"
"""
        )

        with self.assertRaisesRegex(ToolchainConfigError, "unknown fields"):
            load_toolchain_profile(path, host_os="Linux")

    def test_multiple_profiles_for_same_host_require_explicit_selection(self):
        path = self.write_config(
            """
schema_version = 1
[profiles.linux_a]
host_os = "Linux"
[profiles.linux_b]
host_os = "Linux"
"""
        )

        with self.assertRaisesRegex(ToolchainConfigError, "exactly one profile"):
            load_toolchain_profile(path, host_os="Linux")


if __name__ == "__main__":
    unittest.main()
