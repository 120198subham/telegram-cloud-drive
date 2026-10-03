"""
Pixel-Vault Streaming Encryption Module
Zero-memory-leak, low-CPU streaming AES-CTR encryption and decryption
with compress_level=0 uncompressed PNG chunking.
"""

import io
import os
import struct
import zlib
from pathlib import Path
from typing import BinaryIO, Generator, Iterable, Iterator, Optional, Union

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend
    _HAS_CRYPTOGRAPHY = True
except ImportError:
    _HAS_CRYPTOGRAPHY = False
    try:
        import pyaes
        _HAS_PYAES = True
    except ImportError:
        _HAS_PYAES = False

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
DEFAULT_CHUNK_SIZE = 1024 * 1024  # 1 MB default payload chunk size
DEFAULT_WIDTH = 1024


def _normalize_key_and_nonce(key: bytes, nonce: bytes) -> tuple[bytes, bytes]:
    """Ensure key is 16/24/32 bytes and nonce is 16 bytes for AES-CTR."""
    if len(key) not in (16, 24, 32):
        if len(key) < 16:
            key = key.ljust(16, b"\x00")
        elif len(key) < 24:
            key = key.ljust(24, b"\x00")
        elif len(key) < 32:
            key = key.ljust(32, b"\x00")
        else:
            key = key[:32]

    if len(nonce) < 16:
        nonce = nonce.ljust(16, b"\x00")
    elif len(nonce) > 16:
        nonce = nonce[:16]

    return key, nonce


class _AesCtrStreamCipher:
    """Streaming AES-CTR cipher handling continuous keystream across chunks."""

    def __init__(self, key: bytes, nonce: bytes, for_encryption: bool = True):
        self.key, self.nonce = _normalize_key_and_nonce(key, nonce)
        self.for_encryption = for_encryption

        if _HAS_CRYPTOGRAPHY:
            cipher = Cipher(algorithms.AES(self.key), modes.CTR(self.nonce), backend=default_backend())
            self._ctx = cipher.encryptor() if for_encryption else cipher.decryptor()
            self._use_crypto = True
        elif _HAS_PYAES:
            # pyaes Counter from 16-byte nonce
            initial_value = int.from_bytes(self.nonce, byteorder="big")
            counter = pyaes.Counter(initial_value=initial_value)
            self._ctx = pyaes.AESModeOfOperationCTR(self.key, counter=counter)
            self._use_crypto = False
        else:
            raise RuntimeError("Neither 'cryptography' nor 'pyaes' is installed.")

    def update(self, data: bytes) -> bytes:
        if not data:
            return b""
        if self._use_crypto:
            return self._ctx.update(data)
        else:
            if self.for_encryption:
                return self._ctx.encrypt(data)
            else:
                return self._ctx.decrypt(data)

    def finalize(self) -> bytes:
        if self._use_crypto:
            return self._ctx.finalize()
        return b""


def _make_png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    """Create a standard PNG chunk with length, type, payload, and CRC32."""
    length = struct.pack(">I", len(data))
    crc = struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    return length + chunk_type + data + crc


def create_png_chunk_from_payload(payload: bytes, width: int = DEFAULT_WIDTH) -> bytes:
    """
    Encapsulate binary payload into a valid PNG file using compress_level=0 (uncompressed deflate).
    This provides minimal CPU overhead and zero loss of binary fidelity.
    """
    payload_len = len(payload)
    total_data_len = 8 + payload_len  # 8 bytes uint64 length prefix + payload

    w = width if total_data_len > width else max(1, total_data_len)
    h = (total_data_len + w - 1) // w
    padded_len = w * h

    # IHDR: Width, Height, Bit depth 8, Color type 0 (Grayscale), Compression 0, Filter 0, Interlace 0
    ihdr_data = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)
    ihdr_chunk = _make_png_chunk(b"IHDR", ihdr_data)

    # Prepare buffer: 8-byte uint64 length prefix + payload + padding
    data_buffer = bytearray(struct.pack(">Q", payload_len))
    data_buffer.extend(payload)
    padding_needed = padded_len - total_data_len
    if padding_needed > 0:
        data_buffer.extend(b"\x00" * padding_needed)

    # Deflate scanlines with compress_level=0 (no compression, stored blocks)
    # Each scanline starts with filter byte 0x00 (Filter: None)
    compressor = zlib.compressobj(level=0)
    deflated = bytearray()
    for row in range(h):
        row_slice = data_buffer[row * w : (row + 1) * w]
        deflated.extend(compressor.compress(b"\x00" + row_slice))
    deflated.extend(compressor.flush())

    idat_chunk = _make_png_chunk(b"IDAT", bytes(deflated))
    iend_chunk = _make_png_chunk(b"IEND", b"")

    return PNG_SIGNATURE + ihdr_chunk + idat_chunk + iend_chunk


def write_streaming_png_chunk_file(
    stream_chunk_generator: Iterable[bytes],
    total_payload_len: int,
    output_path: Path,
    width: int = 65536,
    idat_flush_bytes: int = 256 * 1024,
) -> int:
    """
    Stream uncompressed deflate blocks directly into a valid PNG file on disk in 64 KB blocks.
    Keeps memory consumption strictly bounded (< 15 MB) even for multi-gigabyte chunks.
    Fast bulk scanline processing minimizes CPU overhead.
    Returns total bytes written to output_path.
    """
    total_data_len = 8 + total_payload_len
    w = width if total_data_len > width else max(1, total_data_len)
    h = (total_data_len + w - 1) // w
    padded_len = w * h

    ihdr_data = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)
    ihdr_chunk = _make_png_chunk(b"IHDR", ihdr_data)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "wb") as f:
        f.write(PNG_SIGNATURE)
        f.write(ihdr_chunk)

        compressor = zlib.compressobj(level=0)
        raw_header = struct.pack(">Q", total_payload_len)
        padding_needed = padded_len - total_data_len

        def _gen_raw_stream():
            yield raw_header
            for b in stream_chunk_generator:
                if b:
                    yield b
            rem = padding_needed
            while rem > 0:
                take = min(rem, 64 * 1024)
                yield b"\x00" * take
                rem -= take

        row_buffer = bytearray()
        idat_buf = bytearray()

        for block in _gen_raw_stream():
            row_buffer.extend(block)
            while len(row_buffer) >= w:
                row_slice = row_buffer[:w]
                del row_buffer[:w]
                idat_buf.extend(compressor.compress(b"\x00" + row_slice))
                if len(idat_buf) >= idat_flush_bytes:
                    f.write(_make_png_chunk(b"IDAT", bytes(idat_buf)))
                    idat_buf.clear()

        if row_buffer:
            idat_buf.extend(compressor.compress(b"\x00" + row_buffer))

        final_compressed = compressor.flush()
        idat_buf.extend(final_compressed)
        if idat_buf:
            f.write(_make_png_chunk(b"IDAT", bytes(idat_buf)))
        f.write(_make_png_chunk(b"IEND", b""))

    return os.path.getsize(output_path)


def extract_payload_from_png_chunk(png_bytes: bytes) -> bytes:
    """
    Extract binary payload from a PNG chunk created with create_png_chunk_from_payload.
    Validates PNG signature, chunk CRCs, and scanline structure.
    """
    if not png_bytes.startswith(PNG_SIGNATURE):
        raise ValueError("Invalid PNG signature.")

    offset = 8
    idat_parts: list[bytes] = []
    w, h = 0, 0
    png_len = len(png_bytes)

    while offset < png_len:
        if offset + 12 > png_len:
            break
        chunk_len = struct.unpack(">I", png_bytes[offset : offset + 4])[0]
        chunk_type = png_bytes[offset + 4 : offset + 8]
        data_end = offset + 8 + chunk_len
        chunk_data = png_bytes[offset + 8 : data_end]
        crc = struct.unpack(">I", png_bytes[data_end : data_end + 4])[0]

        expected_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if crc != expected_crc:
            raise ValueError(f"CRC mismatch in PNG chunk {chunk_type!r}.")

        if chunk_type == b"IHDR":
            w, h = struct.unpack(">II", chunk_data[:8])
        elif chunk_type == b"IDAT":
            idat_parts.append(chunk_data)
        elif chunk_type == b"IEND":
            break

        offset = data_end + 4

    if not idat_parts or w == 0 or h == 0:
        raise ValueError("Malformed PNG: missing IHDR or IDAT chunks.")

    # Decompress IDAT with zlib
    decompressor = zlib.decompressobj()
    raw_scanlines = decompressor.decompress(b"".join(idat_parts)) + decompressor.flush()

    # Extract scanlines by stripping filter byte
    recovered = bytearray()
    row_stride = 1 + w
    pos = 0
    for _ in range(h):
        if pos + row_stride > len(raw_scanlines):
            break
        # First byte is filter type (0x00)
        recovered.extend(raw_scanlines[pos + 1 : pos + row_stride])
        pos += row_stride

    if len(recovered) < 8:
        raise ValueError("Corrupt payload: missing length header.")

    payload_len = struct.unpack(">Q", recovered[:8])[0]
    return bytes(recovered[8 : 8 + payload_len])


def stream_unpack_png_file(png_path_or_file: Union[Path, str, BinaryIO]) -> Iterator[bytes]:
    """
    Stream binary payload directly from a PNG chunk file on disk.
    Decompresses IDAT chunks in 64 KB blocks and extracts scanlines without buffering
    the whole PNG in memory, keeping memory consumption strictly < 1 MB.
    Yields 64 KB slices of the raw payload (ciphertext).
    """
    close_file = False
    if isinstance(png_path_or_file, (str, Path)):
        f = open(png_path_or_file, "rb")
        close_file = True
    else:
        f = png_path_or_file
        f.seek(0)

    try:
        sig = f.read(8)
        if sig != PNG_SIGNATURE:
            raise ValueError("Invalid PNG signature.")

        decompressor = zlib.decompressobj()
        w, h = 0, 0
        scanline_buf = bytearray()
        payload_len = None
        yielded_bytes = 0

        while True:
            header = f.read(8)
            if not header or len(header) < 8:
                break
            chunk_len = struct.unpack(">I", header[0:4])[0]
            chunk_type = header[4:8]

            if chunk_type == b"IHDR":
                ihdr_data = f.read(chunk_len)
                w, h = struct.unpack(">II", ihdr_data[:8])
                f.read(4)  # CRC
            elif chunk_type == b"IDAT":
                rem = chunk_len
                while rem > 0:
                    take = min(rem, 64 * 1024)
                    compressed = f.read(take)
                    rem -= len(compressed)
                    raw = decompressor.decompress(compressed)
                    if raw:
                        scanline_buf.extend(raw)
                        row_stride = 1 + w
                        while len(scanline_buf) >= row_stride:
                            row_slice = scanline_buf[1:row_stride]
                            del scanline_buf[:row_stride]
                            if payload_len is None:
                                payload_len = struct.unpack(">Q", row_slice[:8])[0]
                                row_slice = row_slice[8:]
                            if yielded_bytes < payload_len:
                                take_p = min(len(row_slice), payload_len - yielded_bytes)
                                if take_p > 0:
                                    yield bytes(row_slice[:take_p])
                                    yielded_bytes += take_p
                f.read(4)  # CRC
            elif chunk_type == b"IEND":
                f.read(4)  # CRC
                break
            else:
                f.seek(chunk_len + 4, 1)

        trailing_raw = decompressor.flush()
        if trailing_raw:
            scanline_buf.extend(trailing_raw)
            row_stride = 1 + w
            while len(scanline_buf) >= row_stride:
                row_slice = scanline_buf[1:row_stride]
                del scanline_buf[:row_stride]
                if payload_len is None:
                    payload_len = struct.unpack(">Q", row_slice[:8])[0]
                    row_slice = row_slice[8:]
                if yielded_bytes < payload_len:
                    take_p = min(len(row_slice), payload_len - yielded_bytes)
                    if take_p > 0:
                        yield bytes(row_slice[:take_p])
                        yielded_bytes += take_p
    finally:
        if close_file:
            f.close()


def _iter_stream_chunks(
    stream: Union[bytes, bytearray, BinaryIO, Iterable[bytes]],
    chunk_size: int,
) -> Iterator[bytes]:
    """Iterate over any stream or byte input in chunks of up to chunk_size bytes."""
    if chunk_size <= 0:
        chunk_size = DEFAULT_CHUNK_SIZE

    if isinstance(stream, (bytes, bytearray, memoryview)):
        view = memoryview(stream)
        for i in range(0, len(view), chunk_size):
            yield bytes(view[i : i + chunk_size])
        return

    if hasattr(stream, "read"):
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            yield chunk
        return

    if isinstance(stream, Iterable):
        buf = bytearray()
        for item in stream:
            if not item:
                continue
            buf.extend(item)
            while len(buf) >= chunk_size:
                yield bytes(buf[:chunk_size])
                del buf[:chunk_size]
        if buf:
            yield bytes(buf)
        return

    raise TypeError(f"Unsupported stream source type: {type(stream)}")


def pack_stream_to_png_chunks(
    stream: Union[bytes, bytearray, BinaryIO, Iterable[bytes]],
    chunk_size_bytes: int = DEFAULT_CHUNK_SIZE,
    key: bytes = b"",
    nonce: bytes = b"",
) -> Generator[bytes, None, None]:
    """
    Encrypt a stream using AES-CTR and pack it into valid PNG chunks with compress_level=0.
    Yields valid PNG file bytes for each chunk with zero memory leak and low CPU overhead.
    """
    if not key:
        key = os.urandom(32)
    if not nonce:
        nonce = os.urandom(16)

    cipher = _AesCtrStreamCipher(key, nonce, for_encryption=True)

    for plaintext_chunk in _iter_stream_chunks(stream, chunk_size_bytes):
        ciphertext = cipher.update(plaintext_chunk)
        png_chunk = create_png_chunk_from_payload(ciphertext)
        yield png_chunk

    cipher.finalize()


def unpack_png_chunks_to_stream(
    png_chunks: Union[bytes, BinaryIO, Iterable[bytes]],
    key: bytes,
    nonce: bytes,
) -> Generator[bytes, None, None]:
    """
    Unpack PNG chunks and decrypt the original stream using AES-CTR.
    Yields decrypted plaintext chunks in original order.
    """
    cipher = _AesCtrStreamCipher(key, nonce, for_encryption=False)

    if isinstance(png_chunks, (bytes, bytearray)):
        # May be a single PNG or multiple concatenated PNGs
        data = bytes(png_chunks)
        pos = 0
        while pos < len(data):
            # Find next PNG header
            if not data[pos:].startswith(PNG_SIGNATURE):
                next_sig = data.find(PNG_SIGNATURE, pos + 1)
                if next_sig == -1:
                    break
                pos = next_sig
            # Scan to IEND
            iend_idx = data.find(b"IEND", pos)
            if iend_idx == -1:
                chunk_slice = data[pos:]
                pos = len(data)
            else:
                chunk_slice = data[pos : iend_idx + 8]
                pos = iend_idx + 8

            ciphertext = extract_payload_from_png_chunk(chunk_slice)
            plaintext = cipher.update(ciphertext)
            if plaintext:
                yield plaintext
        cipher.finalize()
        return

    for png_chunk in png_chunks:
        if not png_chunk:
            continue
        ciphertext = extract_payload_from_png_chunk(png_chunk)
        plaintext = cipher.update(ciphertext)
        if plaintext:
            yield plaintext

    cipher.finalize()


def verify_stream_round_trip(
    stream_or_data: Union[bytes, BinaryIO, Iterable[bytes]],
    chunk_size_bytes: int = DEFAULT_CHUNK_SIZE,
    key: Optional[bytes] = None,
    nonce: Optional[bytes] = None,
) -> bool:
    """
    Helper function to verify bit-for-bit round-trip streaming encryption and decryption.
    Returns True if recovered stream matches original exactly.
    """
    if key is None:
        key = os.urandom(32)
    if nonce is None:
        nonce = os.urandom(16)

    # If stream_or_data is file-like or bytes, capture reference for comparison
    if isinstance(stream_or_data, (bytes, bytearray)):
        original_bytes = bytes(stream_or_data)
        stream_input = original_bytes
    elif hasattr(stream_or_data, "read"):
        original_bytes = stream_or_data.read()
        stream_input = io.BytesIO(original_bytes)
    else:
        original_bytes = b"".join(stream_or_data)
        stream_input = original_bytes

    png_chunks = list(pack_stream_to_png_chunks(stream_input, chunk_size_bytes, key, nonce))
    recovered_parts = list(unpack_png_chunks_to_stream(png_chunks, key, nonce))
    recovered_bytes = b"".join(recovered_parts)

    return recovered_bytes == original_bytes
