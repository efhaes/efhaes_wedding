import os
import re
from pathlib import Path
from typing import BinaryIO, Protocol

from app.core.config import settings


_STORAGE_KEY_PATTERN = re.compile(r"^[0-9a-f]{32}\.(?:jpg|png|webp|mp3)$")


class MediaStorage(Protocol):
    def put(self, storage_key: str, data: bytes) -> None: ...

    def put_stream(
        self,
        storage_key: str,
        source: BinaryIO,
        max_bytes: int,
        *,
        chunk_size: int = 64 * 1024,
    ) -> int: ...

    def open(self, storage_key: str) -> BinaryIO: ...

    def discard(self, storage_key: str) -> None: ...

    def stage_delete(self, storage_key: str) -> str: ...

    def restore(self, token: str) -> None: ...

    def purge(self, token: str) -> None: ...


class LocalMediaStorage:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or settings.MEDIA_STORAGE_PATH).expanduser().resolve()
        self.trash = self.root / ".trash"

    def put(self, storage_key: str, data: bytes) -> None:
        destination = self._path(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            with destination.open("xb") as file:
                created = True
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
        except OSError:
            if created:
                try:
                    destination.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    raise RuntimeError(
                        "A partially written media file could not be removed"
                    ) from cleanup_error
            raise

    def put_stream(
        self,
        storage_key: str,
        source: BinaryIO,
        max_bytes: int,
        *,
        chunk_size: int = 64 * 1024,
    ) -> int:
        destination = self._path(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        created = False
        try:
            with destination.open("xb") as target:
                created = True
                while chunk := source.read(chunk_size):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("File exceeds the configured size limit")
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
        except Exception:
            if created:
                try:
                    destination.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    raise RuntimeError(
                        "A partially written media file could not be removed"
                    ) from cleanup_error
            raise
        return total

    def path(self, storage_key: str) -> Path:
        destination = self._path(storage_key)
        if not destination.is_file():
            raise FileNotFoundError("Media file is unavailable")
        return destination

    def open(self, storage_key: str) -> BinaryIO:
        return self.path(storage_key).open("rb")

    def discard(self, storage_key: str) -> None:
        self._path(storage_key).unlink(missing_ok=True)

    def stage_delete(self, storage_key: str) -> str:
        source = self._path(storage_key)
        if not source.is_file():
            raise FileNotFoundError("Media file is unavailable")
        self.trash.mkdir(parents=True, exist_ok=True)
        if self.trash.is_symlink() or self.trash.resolve().parent != self.root:
            raise ValueError("Invalid media quarantine directory")
        os.replace(source, self.trash / storage_key)
        return storage_key

    def restore(self, token: str) -> None:
        self._validate_key(token)
        if self.trash.is_symlink() or self.trash.resolve().parent != self.root:
            raise ValueError("Invalid media quarantine directory")
        quarantined = self.trash / token
        if quarantined.exists():
            os.replace(quarantined, self._path(token))

    def purge(self, token: str) -> None:
        self._validate_key(token)
        if self.trash.is_symlink() or self.trash.resolve().parent != self.root:
            raise ValueError("Invalid media quarantine directory")
        (self.trash / token).unlink(missing_ok=True)

    def _path(self, storage_key: str) -> Path:
        self._validate_key(storage_key)
        if self.root.is_symlink():
            raise ValueError("Storage root cannot be a symbolic link")
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.resolve() != self.root:
            raise ValueError("Storage root resolution changed")
        destination = self.root / storage_key
        if destination.is_symlink() or destination.resolve().parent != self.root:
            raise ValueError("Invalid media storage path")
        return destination

    @staticmethod
    def _validate_key(storage_key: str) -> None:
        if not _STORAGE_KEY_PATTERN.fullmatch(storage_key):
            raise ValueError("Invalid internal media storage key")


def get_media_storage() -> MediaStorage:
    return LocalMediaStorage()
