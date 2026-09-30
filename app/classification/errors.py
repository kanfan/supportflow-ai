"""Safe provider errors: messages contain only stable categories."""

from typing import ClassVar


class ClassificationError(RuntimeError):
    category: ClassVar[str] = "classification_error"
    retryable: ClassVar[bool] = False

    def __init__(self) -> None:
        super().__init__(self.category)


class ProviderTimeoutError(ClassificationError):
    category = "provider_timeout"
    retryable = True


class ProviderUnavailableError(ClassificationError):
    category = "provider_unavailable"
    retryable = True


class ProviderRateLimitedError(ClassificationError):
    category = "provider_rate_limited"
    retryable = True


class ProviderAuthenticationError(ClassificationError):
    category = "provider_authentication"


class InvalidProviderOutputError(ClassificationError):
    category = "invalid_provider_output"


class InputTooLargeError(ClassificationError):
    category = "input_too_large"
