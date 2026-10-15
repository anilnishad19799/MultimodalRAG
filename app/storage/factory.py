from app.core.config import Settings
from app.storage.minio_storage import MinioStorage


def create_storage(settings: Settings):
    return MinioStorage(
        endpoint=settings.s3_endpoint,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key.get_secret_value(),
        bucket=settings.s3_bucket,
        secure=settings.s3_secure,
    )
