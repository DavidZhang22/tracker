"""One-shot document parser. No shell, network requests, credentials, or saved files."""

import json
import sys


def main():
    if sys.platform != "win32":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (384 * 1024**2, 384 * 1024**2))
        resource.setrlimit(resource.RLIMIT_CPU, (12, 12))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    from .file_import import parse_file
    from .limits import MAX_CSV_BYTES, MAX_PREVIEW_BYTES
    from .urls import DiscoveryError

    try:
        header = sys.stdin.buffer.readline(8193)
        if len(header) > 8192 or not header.endswith(b"\n"):
            raise DiscoveryError("Invalid import options.")
        options = json.loads(header)
        data = sys.stdin.buffer.read(MAX_CSV_BYTES + 1)
        payload = parse_file(data, **options)
        output = json.dumps({"result": payload}, ensure_ascii=False).encode()
        if len(output) > MAX_PREVIEW_BYTES:
            raise DiscoveryError(
                "The extracted preview is too large. Split the input into smaller files."
            )
    except DiscoveryError as exc:
        output = json.dumps({"error": str(exc)}).encode()
    except (MemoryError, RecursionError):
        output = b'{"error":"This document exceeds the processing limit. Split it into smaller files."}'
    except Exception:
        # Third-party parser exceptions can contain document contents or paths.
        output = b'{"error":"This file could not be read. Export it again or paste its text."}'
    sys.stdout.buffer.write(output)


if __name__ == "__main__":
    main()
