import ast
import os
import sys
from pathlib import Path

import pytest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


PROTOCOL_06_CAPTURE_PATH = Path(ROOT) / "examples" / "grotttest.py"


@pytest.fixture(scope="session")
def sanitized_protocol_06_capture():
    tree = ast.parse(PROTOCOL_06_CAPTURE_PATH.read_text(encoding="utf-8"))
    capture_hex = next(
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "xdata"
            for target in node.targets
        )
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )
    return bytes.fromhex(capture_hex)


@pytest.fixture(scope="session")
def decrypt_protocol_06_capture():
    def decrypt(frame):
        plaintext = bytearray(frame[:-2])
        mask = b"Growatt"
        for index in range(8, len(plaintext)):
            plaintext[index] ^= mask[(index - 8) % len(mask)]
        return bytes(plaintext)

    return decrypt
