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
]
