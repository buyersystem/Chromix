"""Bind DevTools shaped-run table digests to bounded SFNT/TTC file evidence.

Matching names is never enough. A match proves table content of a used typeface,
not DirectWrite/FreeType equivalence, hinting, variation state or every glyph.
"""
import hashlib
from pathlib import Path
import re
import struct

ALGORITHM = "chromix-font-tables-v1-sha256"
PREFIX = b"chromix-font-tables-v1\0"
MAX_FILE = 128 * 1024 * 1024


def canonical_table_bytes(tables):
    """Independent encoder for the native DevTools digest contract."""
    if not 1 <= len(tables) <= 512:
        raise ValueError("invalid font table count")
    total = 0
    yield PREFIX
    for tag, data in sorted(tables.items()):
        if not isinstance(tag, bytes) or len(tag) != 4:
            raise ValueError("invalid font table tag")
        if tag == b"DSIG":
            continue
        if not 0 < len(data) <= 64 * 1024 * 1024 or total + len(data) > MAX_FILE:
            raise ValueError("invalid font table size")
        total += len(data)
        if tag == b"head":
            if len(data) < 12:
                raise ValueError("truncated head table")
            data = data[:8] + b"\0" * 4 + data[12:]
        yield tag
        yield struct.pack(">Q", len(data))
        yield data


def table_hash(tables):
    digest = hashlib.sha256()
    for data in canonical_table_bytes(tables):
        digest.update(data)
    return digest.hexdigest()


def _face(data, offset):
    if offset < 0 or offset + 12 > len(data) or data[offset:offset + 4] not in (b"\0\1\0\0", b"OTTO", b"true", b"typ1"):
        raise ValueError("invalid SFNT header")
    count = struct.unpack_from(">H", data, offset + 4)[0]
    directory_end = offset + 12 + count * 16
    if not 1 <= count <= 512 or directory_end > len(data):
        raise ValueError("invalid SFNT directory")
    tables, intervals = {}, []
    for index in range(count):
        tag, _, start, size = struct.unpack_from(">4sIII", data, offset + 12 + index * 16)
        if tag in tables or not size or size > 64 * 1024 * 1024 or start + size > len(data):
            raise ValueError("invalid SFNT table bounds or duplicate tag")
        if start < directory_end and start + size > offset:
            raise ValueError("font table overlaps its directory")
        tables[tag] = data[start:start + size]
        intervals.append((start, start + size))
    intervals.sort()
    if any(left[1] > right[0] for left, right in zip(intervals, intervals[1:])):
        raise ValueError("font tables overlap")
    return {"table_hash": table_hash(tables), "algorithm": ALGORITHM, "table_count": count}


def font_file_record(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("font input must be a regular non-symlink file")
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE + 1)
    if len(data) > MAX_FILE or len(data) < 12:
        raise ValueError("font file size out of bounds")
    offsets = [0]
    if data[:4] == b"ttcf":
        version, count = struct.unpack_from(">II", data, 4)
        if version not in (0x00010000, 0x00020000) or not 1 <= count <= 256 or 12 + count * 4 > len(data):
            raise ValueError("invalid TTC directory")
        offsets = list(struct.unpack_from(">" + "I" * count, data, 12))
        if len(set(offsets)) != len(offsets) or min(offsets) < 12 + count * 4:
            raise ValueError("invalid TTC face offsets")
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data), "faces": [{"face_index": index, **_face(data, offset)}
                                        for index, offset in enumerate(offsets)]}


def bind_font_sources(samples, files):
    index, errors = {}, []
    valid_hash = lambda value: isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))
    if not isinstance(files, list) or not isinstance(samples, list):
        raise ValueError("font files and source samples must be arrays")
    for record in files:
        if (not isinstance(record, dict) or not isinstance(record.get("path"), str) or
                not record["path"] or "\0" in record["path"] or not valid_hash(record.get("sha256")) or
                type(record.get("size")) is not int or not 12 <= record["size"] <= MAX_FILE or
                not isinstance(record.get("faces"), list) or not 1 <= len(record["faces"]) <= 256):
            errors.append("invalid font-file receipt")
            continue
        for position, face in enumerate(record["faces"]):
            if (not isinstance(face, dict) or face.get("algorithm") != ALGORITHM or
                    not valid_hash(face.get("table_hash")) or type(face.get("face_index")) is not int or
                    face["face_index"] != position or type(face.get("table_count")) is not int or
                    not 1 <= face["table_count"] <= 512):
                errors.append("invalid font face receipt")
                continue
            index.setdefault(face["table_hash"], []).append({"path": record["path"],
                "file_sha256": record["sha256"], "face_index": face["face_index"]})
    bindings, unavailable = [], []
    for sample_index, sample in enumerate(samples):
        if not isinstance(sample, dict) or not isinstance(sample.get("platformFonts"), list):
            errors.append(f"sample {sample_index}: invalid shaped-run observations")
            continue
        fonts = sample.get("platformFonts", [])
        if not fonts:
            errors.append(f"sample {sample_index}: no shaped-run font observations")
        for font in fonts:
            if (not isinstance(font, dict) or type(font.get("glyphCount")) is not int or
                    font["glyphCount"] <= 0):
                errors.append(f"sample {sample_index}: missing positive shaped glyph count")
                continue
            binding = {"sample_index": sample_index, "postscript_name": font.get("postScriptName"),
                       "glyph_count": font.get("glyphCount"), "candidates": []}
            digest = font.get("fontTableHash")
            algorithm = font.get("fontTableHashAlgorithm")
            if digest is None and algorithm is None:
                binding["status"] = "native_digest_unavailable"
                unavailable.append(f"sample {sample_index}: native typeface digest absent")
            elif algorithm != ALGORITHM or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                binding["status"] = "invalid_digest"
                errors.append(f"sample {sample_index}: unsupported or malformed typeface digest")
            else:
                binding["table_hash"] = digest
                binding["candidates"] = index.get(digest, [])
                if not binding["candidates"]:
                    binding["status"] = "unmatched"
                    unavailable.append(f"sample {sample_index}: no font-file content match")
                elif len(binding["candidates"]) > 1:
                    binding["status"] = "content_matched_multiple_files"
                    unavailable.append(f"sample {sample_index}: content matches multiple file faces")
                else:
                    binding["status"] = "file_content_bound"
            bindings.append(binding)
    if not samples or not bindings:
        errors.append("no font samples were collected")
    return {"algorithm": ALGORITHM, "bindings": bindings, "errors": errors, "unavailable": unavailable,
            "file_binding_verified": bool(bindings) and not errors and not unavailable,
            "rasterization_equivalence": "not_verified",
            "status": "failed" if errors else "incomplete" if unavailable else "passed"}
