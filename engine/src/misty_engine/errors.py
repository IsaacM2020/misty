"""Error hierarchy. str(error) is the code only, never content."""


class MistyError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code

    def __str__(self) -> str:
        return self.code


class Retryable(MistyError):
    """Network blip, 429, 5xx: retry with backoff."""


class NetworkDown(MistyError):
    def __init__(self, code: str = "NETWORK_DOWN"):
        super().__init__(code)


class CapHit(MistyError):
    """Spend cap would be crossed."""


class Fatal(MistyError):
    """Do not retry automatically."""


class PrivacyViolation(Fatal):
    pass


class SchemaUnrecognized(Fatal):
    pass


class ExtractError(Fatal):
    pass
