"""Read JSON records carried by Next.js Flight; never evaluate JavaScript."""

import json
import re


class FlightData:
    def __init__(self, soup):
        decoder = json.JSONDecoder()
        chunks = []
        size = 0
        for script in soup.find_all("script", limit=128):
            text = script.string or script.get_text()
            for match in re.finditer(
                r"(?:self|window)\.__next_f\.push\(\s*(?=\[)", text
            ):
                try:
                    row, _ = decoder.raw_decode(text, match.end())
                except (ValueError, RecursionError):
                    continue
                if (
                    isinstance(row, list)
                    and len(row) == 2
                    and row[0] == 1
                    and isinstance(row[1], str)
                ):
                    size += len(row[1])
                    if size > 8_000_000:
                        break
                    chunks.append(row[1])
            if size > 8_000_000:
                break
        self.frames = {}
        for line in "".join(chunks).splitlines()[:10000]:
            key, _, value = line.partition(":")
            if not re.fullmatch(r"[0-9a-f]+", key):
                continue
            try:
                self.frames[key] = json.loads(value)
            except (ValueError, RecursionError):
                continue  # Module references and non-JSON protocol records.

    def resolve(self, value):
        visited = set()
        for _ in range(12):
            if not isinstance(value, str) or not re.fullmatch(
                r"\$[0-9a-f]+(?::[^:]+)*", value
            ):
                return value
            if value in visited:
                return None
            visited.add(value)
            keys = value[1:].split(":")
            value = self.frames.get(keys[0])
            for key in keys[1:]:
                if isinstance(value, dict):
                    value = value.get(key)
                elif (
                    isinstance(value, list)
                    and len(value) == 4
                    and value[0] == "$"
                    and key == "props"
                ):
                    value = value[3]  # A serialized React element's props field.
                elif (
                    isinstance(value, list)
                    and key.isdigit()
                    and len(key) <= 6
                    and int(key) < len(value)
                ):
                    value = value[int(key)]
                else:
                    return None
        return None

    def objects(self):
        stack = list(self.frames.values())
        for _ in range(50000):
            if not stack:
                break
            value = stack.pop()
            if isinstance(value, dict):
                yield value
                stack.extend(v for v in value.values() if isinstance(v, (dict, list)))
            elif isinstance(value, list):
                stack.extend(v for v in value if isinstance(v, (dict, list)))
