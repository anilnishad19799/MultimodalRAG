class RAGError(Exception):
    """Expected application error suitable for a safe API response."""


class InvalidUpload(RAGError):
    pass


class ProviderError(RAGError):
    pass


class StorageError(RAGError):
    pass
