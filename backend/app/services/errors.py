"""Service-level errors. main.py turns these into HTTP responses."""


class ServiceError(Exception):
    status_code = 500

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class AINotConfiguredError(ServiceError):
    status_code = 503  # server isn't set up for AI yet


class AIProviderError(ServiceError):
    status_code = 502  # the AI provider failed


class AIMalformedResponseError(ServiceError):
    status_code = 502  # the AI answered, but not with valid structured JSON


class UnreadableFileError(ServiceError):
    status_code = 422
