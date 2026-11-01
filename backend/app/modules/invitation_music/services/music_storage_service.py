import os
import re
from pathlib import PurePath
from typing import BinaryIO
from uuid import uuid4

from fastapi import HTTPException, status

from app.core.config import settings
from app.modules.media.storage import MediaStorage


_STORAGE_KEY_PATTERN = re.compile(r"^[0-9a-f]{32}\.mp3$")
_MPEG1_LAYER3_BITRATES = (
    0,
    32,
    40,
    48,
    56,
    64,
    80,
    96,
    112,
    128,
    160,
    192,
    224,
    256,
    320,
    0,
)
_MPEG2_LAYER3_BITRATES = (
    0,
    8,
    16,
    24,
    32,
    40,
    48,
    56,
    64,
    80,
    96,
    112,
    128,
    144,
    160,
    0,
)
_SAMPLE_RATES = (44100, 48000, 32000)


class MusicStorageService:
    def __init__(self, storage: MediaStorage) -> None:
        self.storage = storage
        self.max_file_size = settings.MUSIC_MAX_FILE_SIZE_MB * 1024 * 1024
        self.max_duration_seconds = settings.MUSIC_MAX_DURATION_SECONDS

    def store_upload(
        self,
        source: BinaryIO,
        filename: str,
        content_type: str | None,
    ) -> tuple[str, str, int, float]:
        extension = PurePath(filename.replace("\\", "/").rsplit("/", 1)[-1]).suffix.lower()
        mime_type = (content_type or "").split(";", 1)[0].strip().lower()
        if extension != ".mp3":
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Only MP3 audio files are allowed",
            )
        if mime_type != "audio/mpeg":
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Content-Type must be audio/mpeg",
            )
        storage_key = f"{uuid4().hex}.mp3"
        try:
            file_size = self.storage.put_stream(
                storage_key,
                source,
                self.max_file_size,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"MP3 size must not exceed {settings.MUSIC_MAX_FILE_SIZE_MB} MiB",
            ) from error
        except OSError as error:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Music file could not be stored",
            ) from error

        if file_size == 0:
            self._discard_after_validation_failure(storage_key)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Music file must not be empty",
            )

        try:
            with self.storage.open(storage_key) as stored_file:
                duration = self._inspect_mp3(stored_file, file_size)
        except (OSError, ValueError) as error:
            self._discard_after_validation_failure(storage_key)
            if isinstance(error, HTTPException):
                raise
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Uploaded file is not a valid MP3 audio stream",
            ) from error
        except HTTPException:
            self._discard_after_validation_failure(storage_key)
            raise

        if duration > self.max_duration_seconds:
            self._discard_after_validation_failure(storage_key)
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=(
                    "MP3 duration must not exceed "
                    f"{settings.MUSIC_MAX_DURATION_SECONDS} seconds"
                ),
            )

        safe_filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        if (
            not safe_filename
            or len(safe_filename) > 255
            or any(ord(char) < 32 or ord(char) == 127 for char in safe_filename)
        ):
            self._discard_after_validation_failure(storage_key)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="A valid original filename is required",
            )
        return storage_key, safe_filename, file_size, duration

    def open(self, storage_key: str) -> BinaryIO:
        self._validate_storage_key(storage_key)
        return self.storage.open(storage_key)

    def discard(self, storage_key: str) -> None:
        self._validate_storage_key(storage_key)
        self.storage.discard(storage_key)

    def stage_delete(self, storage_key: str) -> str:
        self._validate_storage_key(storage_key)
        return self.storage.stage_delete(storage_key)

    def restore(self, token: str) -> None:
        self._validate_storage_key(token)
        self.storage.restore(token)

    def purge(self, token: str) -> None:
        self._validate_storage_key(token)
        self.storage.purge(token)

    def _discard_after_validation_failure(self, storage_key: str) -> None:
        try:
            self.discard(storage_key)
        except OSError as error:
            raise RuntimeError(
                "Invalid uploaded music could not be removed from storage"
            ) from error

    @staticmethod
    def _validate_storage_key(storage_key: str) -> None:
        if not _STORAGE_KEY_PATTERN.fullmatch(storage_key):
            raise ValueError("Invalid internal music storage key")

    @staticmethod
    def _inspect_mp3(source: BinaryIO, file_size: int) -> float:
        source.seek(0)
        header = source.read(10)
        if header.startswith(b"ID3"):
            if len(header) != 10 or any(byte & 0x80 for byte in header[6:10]):
                raise ValueError("Invalid ID3 header")
            tag_size = (
                (header[6] << 21)
                | (header[7] << 14)
                | (header[8] << 7)
                | header[9]
            )
            offset = 10 + tag_size + (10 if header[5] & 0x10 else 0)
            if offset >= file_size:
                raise ValueError("ID3 tag contains no audio frames")
            source.seek(offset)
        else:
            source.seek(0)

        frame_count = 0
        total_samples = 0
        sample_rate_for_version: int | None = None
        while source.tell() + 4 <= file_size:
            header_bytes = source.read(4)
            if len(header_bytes) < 4:
                break
            word = int.from_bytes(header_bytes, "big")
            if (word >> 21) & 0x7FF != 0x7FF:
                if frame_count == 0:
                    raise ValueError("MP3 frame sync was not found")
                source.seek(-4, os.SEEK_CUR)
                tail = source.read()
                if tail.startswith(b"TAG") and len(tail) == 128:
                    break
                raise ValueError("Unexpected data after MP3 frames")

            version = (word >> 19) & 0b11
            layer = (word >> 17) & 0b11
            bitrate_index = (word >> 12) & 0b1111
            sample_rate_index = (word >> 10) & 0b11
            padding = (word >> 9) & 1
            if version == 0b01 or layer != 0b01 or bitrate_index in (0, 15):
                raise ValueError("Unsupported or invalid MPEG frame")
            if sample_rate_index == 3:
                raise ValueError("Invalid MPEG sample rate")

            bitrate_table = (
                _MPEG1_LAYER3_BITRATES
                if version == 0b11
                else _MPEG2_LAYER3_BITRATES
            )
            bitrate = bitrate_table[bitrate_index] * 1000
            sample_rate = _SAMPLE_RATES[sample_rate_index]
            if version == 0b10:
                sample_rate //= 2
            elif version == 0b00:
                sample_rate //= 4
            coefficient = 144 if version == 0b11 else 72
            frame_length = coefficient * bitrate // sample_rate + padding
            if frame_length < 4 or source.tell() - 4 + frame_length > file_size:
                raise ValueError("Truncated MPEG frame")

            if sample_rate_for_version is not None and sample_rate != sample_rate_for_version:
                raise ValueError("MP3 stream changes sample rate")
            sample_rate_for_version = sample_rate
            source.seek(frame_length - 4, os.SEEK_CUR)
            frame_count += 1
            total_samples += 1152 if version == 0b11 else 576

        if frame_count == 0 or sample_rate_for_version is None:
            raise ValueError("MP3 stream contains no audio frames")
        return total_samples / sample_rate_for_version
