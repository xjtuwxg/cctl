class CctlError(Exception):
    """User-facing error. Message is printed to stderr, exit code 1."""

    exit_code = 1

    def __init__(self, message, hint=None, exit_code=None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        if exit_code is not None:
            self.exit_code = exit_code


class PolicyDenied(CctlError):
    """The site's policy rejected the call (usually an application credential
    that lacks the `reader` role or the `unrestricted` flag)."""

    exit_code = 3


class NotFound(CctlError):
    exit_code = 4


class Ambiguous(CctlError):
    exit_code = 5
