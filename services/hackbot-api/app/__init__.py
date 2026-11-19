from importlib.metadata import PackageNotFoundError, version

# The version lives in services/hackbot-api/VERSION; the package build reads it.
try:
    __version__ = version("hackbot-api")
except PackageNotFoundError:  # running from a source tree without installing
    __version__ = "0+unknown"
