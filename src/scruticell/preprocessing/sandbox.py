"""Subprocess-isolated sandbox for executing LLM-generated code.

Runs a generated function in a spawned child process with:
  - process-level timeout (terminate + kill on timeout; no residual threads)
  - restricted builtins (no ``__import__`` escape, no ``open``/``exec``/``eval``)
  - import whitelist enforced via a custom ``__import__`` hook
  - result returned via ``multiprocessing`` queue

Shared by the preprocessing tools (extraction, reader codegen, demux splitting).
"""

from __future__ import annotations

import multiprocessing as mp
import traceback
from typing import Any

# Modules generated code may import directly. Anything else raises ImportError.
DEFAULT_ALLOWED_IMPORTS = frozenset({
    "pandas", "numpy", "scipy", "re", "json", "collections", "math",
    "gzip", "csv", "io", "os.path", "anndata", "scanpy",
    "pyreadr", "loompy", "h5py", "pytest",
})

# Restricted builtins exposed to generated code.
_RESTRICTED_BUILTINS = {
    "True": True, "False": False, "None": None,
    "int": int, "float": float, "str": str, "bool": bool,
    "list": list, "dict": dict, "set": set, "tuple": tuple,
    "len": len, "range": range, "enumerate": enumerate,
    "zip": zip, "map": map, "filter": filter,
    "isinstance": isinstance, "issubclass": issubclass, "hasattr": hasattr,
    "getattr": getattr, "setattr": setattr, "abs": abs, "min": min, "max": max,
    "sum": sum, "sorted": sorted, "reversed": reversed, "any": any, "all": all,
    "print": print, "Exception": Exception, "ValueError": ValueError,
    "TypeError": TypeError, "KeyError": KeyError, "IndexError": IndexError,
    "StopIteration": StopIteration, "MemoryError": MemoryError,
    "dict_keys": type({}.keys()), "dict_values": type({}.values()),
}


def _make_import_hook(allowed: frozenset):
    """Build an import hook that only allows whitelisted top-level modules."""
    def _import(name, globals=None, locals=None, fromlist=(), level=0):
        root = name.split(".")[0]
        if root in allowed or name in allowed:
            return __import__(name, globals, locals, fromlist, level)
        raise ImportError(f"import not allowed: {name!r}")
    return _import


def _worker(code, func_name, args, allowed, queue):
    """Child-process entrypoint. Picklable; module-level so spawn works."""
    try:
        builtins = dict(_RESTRICTED_BUILTINS)
        builtins["__import__"] = _make_import_hook(allowed)
        namespace: dict[str, Any] = {"__builtins__": builtins}
        exec(code, namespace)
        func = namespace.get(func_name)
        if func is None or not callable(func):
            queue.put({"ok": False, "error": f"{func_name}() not found in generated code"})
            return
        result = func(*args)
        queue.put({"ok": True, "result": result})
    except Exception as exc:
        queue.put({"ok": False, "error": f"{exc}\n{traceback.format_exc()[-800:]}"})


def run_code_in_subprocess(
    code: str,
    func_name: str,
    args: tuple | list = (),
    allowed_imports: frozenset | set | None = None,
    timeout: float = 60,
) -> dict:
    """Execute ``func_name(*args)`` defined in ``code`` inside a child process.

    Args:
        code: Python source defining the target function.
        func_name: name of the function to call.
        args: positional args passed to the function.
        allowed_imports: import whitelist (defaults to DEFAULT_ALLOWED_IMPORTS).
        timeout: process-level timeout in seconds.

    Returns:
        {"ok": True, "result": <function return>} or
        {"ok": False, "error": "<message>"} on compile/runtime/timeout failure.
    """
    allowed = frozenset(allowed_imports or DEFAULT_ALLOWED_IMPORTS)
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    proc = ctx.Process(target=_worker, args=(code, func_name, args, allowed, queue), daemon=True)
    proc.start()
    proc.join(timeout)
    if proc.is_alive():
        proc.terminate()
        proc.join(5)
        if proc.is_alive():
            proc.kill()
            proc.join(5)
        return {"ok": False, "error": f"Sandbox timed out ({timeout}s)"}
    try:
        return queue.get(timeout=5)
    except Exception:
        return {"ok": False, "error": "Sandbox produced no result"}
