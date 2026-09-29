"""Format-neutral rejection raised by every bounded report parser."""


class ReportSecurityError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
