import threading

from minio import Minio
from minio.error import S3Error

from app.core.errors import StorageError
from app.storage.base import ObjectStorage


class MinioStorage(ObjectStorage):
    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str, secure: bool = False):
        self.client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        self.bucket = bucket
        self._bucket_ready = False
        self._bucket_lock = threading.Lock()

    def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        with self._bucket_lock:
            if self._bucket_ready:
                return
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
            self._bucket_ready = True

    def put(self, key: str, data: bytes, content_type: str) -> None:
        try:
            self._ensure_bucket()
            from io import BytesIO

            self.client.put_object(self.bucket, key, BytesIO(data), len(data), content_type=content_type)
        except S3Error as exc:
            raise StorageError(f"Unable to store uploaded asset: {exc.code}") from exc

    def get(self, key: str) -> bytes:
        try:
            response = self.client.get_object(self.bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except S3Error as exc:
            raise StorageError(f"Unable to read stored asset: {exc.code}") from exc

    def delete(self, key: str) -> None:
        try:
            self.client.remove_object(self.bucket, key)
        except S3Error as exc:
            raise StorageError(f"Unable to delete stored asset: {exc.code}") from exc
