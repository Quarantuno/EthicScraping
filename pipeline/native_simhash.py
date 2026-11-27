"""Optional loader for the Rust SimHash accumulator."""
import ctypes
import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def load_accumulator():
    path = os.environ.get("SCRAPELLM_SIMHASH_LIB")
    if not path:
        return None

    function = ctypes.CDLL(str(Path(path).resolve())).simhash64
    function.argtypes = [ctypes.POINTER(ctypes.c_uint64), ctypes.c_size_t]
    function.restype = ctypes.c_uint64
    return function
