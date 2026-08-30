"""Trusted Android laboratory integration owned by the CausalPerf runner."""

from .environment import (
    AndroidEnvironmentCollector,
    AndroidLabRequirements,
    CommandOutput,
    PreflightResult,
    SubprocessCommandRunner,
)
from .adapter import PreflightExecutionAdapter
from .toolchain import (
    ResolvedToolchain,
    ToolchainConfigError,
    ToolchainProfile,
    load_toolchain_profile,
    resolve_toolchain,
)
from .build import (
    GradleBuildAdapter,
    GradleBuildAttempt,
    GradleBuildExecutionAdapter,
    GradleBuildRequest,
    digest_tree,
)
from .process import ProcessOutput, ProcessSpec, ProcessTransport, SubprocessTransport
from .correctness import (
    CorrectnessAttempt,
    CorrectnessEvidenceRequest,
    CorrectnessReportParser,
    JUnitCounts,
    parse_junit_documents,
    result_artifact_digest,
)
from .correctness_runner import (
    CorrectnessExecutionAdapter,
    GradleCorrectnessRequest,
    GradleCorrectnessRunAttempt,
    GradleCorrectnessRunner,
)
from .device import (
    AdbCleanupExecutionAdapter,
    AdbCleanupRequest,
    AdbDeviceAdapter,
    AdbInstallExecutionAdapter,
    AdbInstallRequest,
    DeviceOperationAttempt,
)
from .dry_run import (
    AndroidDryRunCoordinator,
    AndroidDryRunExecution,
    AndroidDryRunPlan,
)
from .benchmark import (
    BenchmarkRunAttempt,
    GradleBenchmarkExecutionAdapter,
    GradleBenchmarkRequest,
    GradleBenchmarkRunner,
)

__all__ = [
    "AndroidEnvironmentCollector",
    "AndroidLabRequirements",
    "CommandOutput",
    "PreflightResult",
    "SubprocessCommandRunner",
    "PreflightExecutionAdapter",
    "ResolvedToolchain",
    "ToolchainConfigError",
    "ToolchainProfile",
    "load_toolchain_profile",
    "resolve_toolchain",
    "GradleBuildAdapter",
    "GradleBuildAttempt",
    "GradleBuildExecutionAdapter",
    "GradleBuildRequest",
    "digest_tree",
    "ProcessOutput",
    "ProcessSpec",
    "ProcessTransport",
    "SubprocessTransport",
    "CorrectnessAttempt",
    "CorrectnessEvidenceRequest",
    "CorrectnessReportParser",
    "JUnitCounts",
    "parse_junit_documents",
    "result_artifact_digest",
    "CorrectnessExecutionAdapter",
    "GradleCorrectnessRequest",
    "GradleCorrectnessRunAttempt",
    "GradleCorrectnessRunner",
    "AdbCleanupExecutionAdapter",
    "AdbCleanupRequest",
    "AdbDeviceAdapter",
    "AdbInstallExecutionAdapter",
    "AdbInstallRequest",
    "DeviceOperationAttempt",
    "AndroidDryRunCoordinator",
    "AndroidDryRunExecution",
    "AndroidDryRunPlan",
    "BenchmarkRunAttempt",
    "GradleBenchmarkExecutionAdapter",
    "GradleBenchmarkRequest",
    "GradleBenchmarkRunner",
]
