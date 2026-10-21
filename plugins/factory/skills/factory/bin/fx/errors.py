class FactoryError(Exception):
    """A refusal or failure with a message meant for the orchestrator. Exits non-zero."""

    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


class Fallback(FactoryError):
    """codex-review could not run Codex; the orchestrator spawns codex-fallback instead."""
