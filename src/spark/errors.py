class SparkError(Exception):
    exit_code = 1


class ConfigError(SparkError):
    exit_code = 2


class PluginError(SparkError):
    """A plugin manifest or install operation is invalid."""

    exit_code = 2


class PathEscapeError(SparkError):
    """Raised when a tool path resolves outside the workdir."""
