"""Speech on an NVIDIA GPU (owner decision 2026-11-11, the desktop's RTX 4070): where NVIDIA's runtime libraries are,
whether speech can use the GPU, and loading a faster-whisper model on it with the CPU as the fallback. No Qt.

faster-whisper's engine (ctranslate2) runs on CUDA in float16 when it finds cuBLAS 12 and cuDNN 9. Jarvis never
installs them: the owner runs ``pip_line()`` (NVIDIA's own wheels, nvidia-cublas-cu12 and nvidia-cudnn-cu12, which
put the libraries under site-packages\\nvidia\\...\\bin). Only the speech child processes (talk_speech,
meeting_speech) ever load CUDA, so a driver or library failure can never take the app down: ``prepare()`` puts the
libraries' folders on that process's DLL search path, and ``load_model`` tries the GPU first - warmed once on half a
second of quiet noise (``warm_up``: cuDNN loads only at the first use) - and on any failure loads the CPU's int8
model instead, exactly as before.

The app's own process only reads the file system (``status``: the NVIDIA driver's CUDA library in System32 and the
two libraries' files) and words it for Settings (``runs_on_text``). Nothing here logs anything.

Public API
==========
``DEVICES`` ("auto": the GPU when it works; "cpu": never), ``status(device)`` -> READY / NO_GPU / ADDONS_MISSING /
CPU_CHOSEN, ``runs_on_text(state, reported)``, ``pip_line()``, ``cuda_dirs()``, ``libraries_found()``,
``driver_found()``, ``prepare()``, ``cuda_devices()``, ``load_model(factory, folder, device=, threads=, warm=)`` ->
(model, CUDA or CPU), ``warm_up(model)``.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Callable, Iterable, MutableMapping
from pathlib import Path
from typing import Any

DEVICE_AUTO, DEVICE_CPU = "auto", "cpu"
DEVICES = (DEVICE_AUTO, DEVICE_CPU)
READY, NO_GPU, ADDONS_MISSING, CPU_CHOSEN = "ready", "no_gpu", "addons_missing", "cpu_chosen"
CUDA, CPU = "cuda", "cpu"
PACKAGES = ('"nvidia-cublas-cu12==12.*"', '"nvidia-cudnn-cu12==9.*"')
LIBRARIES = ("cublas64_12.dll", "cudnn64_9.dll")       # what ctranslate2 4.x loads on Windows
_PACKAGE_DIRS = (("cublas", "bin"), ("cudnn", "bin"))


def package_roots() -> list[Path]:
    """The folders of the installed ``nvidia`` namespace package (NVIDIA's pip wheels); [] without one."""
    try:
        spec = importlib.util.find_spec("nvidia")
    except (ImportError, ValueError):
        return []
    if spec is None:
        return []
    return [Path(location) for location in (spec.submodule_search_locations or [])]


def cuda_dirs(roots: Iterable[Path] | None = None) -> list[Path]:
    """The cuBLAS and cuDNN ``bin`` folders that exist under ``roots`` (the nvidia package's folders)."""
    found: list[Path] = []
    for root in (package_roots() if roots is None else roots):
        for parts in _PACKAGE_DIRS:
            folder = Path(root).joinpath(*parts)
            if folder.is_dir() and folder not in found:
                found.append(folder)
    return found


def libraries_found(dirs: Iterable[Path] | None = None, path_env: str | None = None) -> bool:
    """cuBLAS 12 and cuDNN 9 are there: in ``dirs`` (the pip wheels' folders) or on PATH."""
    path_text = os.environ.get("PATH", "") if path_env is None else path_env
    places = [*(cuda_dirs() if dirs is None else dirs), *(Path(part) for part in path_text.split(os.pathsep) if part)]
    return all(any((place / name).is_file() for place in places) for name in LIBRARIES)


def driver_found(system32: Path | None = None) -> bool:
    """NVIDIA's driver is installed (its CUDA library, nvcuda.dll, in System32)."""
    if system32 is None and sys.platform != "win32":
        return False
    folder = system32 or Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    return (Path(folder) / "nvcuda.dll").is_file()


def status(device: str = DEVICE_AUTO, *, driver: Callable[[], bool] = driver_found,
           libraries: Callable[[], bool] = libraries_found) -> str:
    """Whether speech can use the GPU, for Settings: READY, NO_GPU, ADDONS_MISSING or CPU_CHOSEN (file reads only)."""
    if device == DEVICE_CPU:
        return CPU_CHOSEN
    if not driver():
        return NO_GPU
    if not libraries():
        return ADDONS_MISSING
    return READY


def pip_line(python: str | None = None) -> str:
    """The command that installs NVIDIA's two libraries, for the owner to run (python.exe, never pythonw.exe)."""
    path = Path(python or sys.executable)
    if path.name.lower() == "pythonw.exe":
        path = path.with_name("python.exe")
    text = str(path).replace("\\", "/")
    head = f'& "{text}"' if " " in text else text
    return f"{head} -m pip install {' '.join(PACKAGES)}"


def runs_on_text(state: str, reported: str = "", *, setting: str = "device") -> str:
    """Settings' "Runs on" row: ``state`` from status(), ``reported`` the device the speech process last said it ran
    on (CUDA, CPU or "" before its first use)."""
    if state == CPU_CHOSEN:
        return f'the CPU ({setting} = "cpu" in config.toml)'
    if state == NO_GPU:
        return "the CPU (no NVIDIA GPU found)"
    if state == ADDONS_MISSING:
        return f"the CPU - for the GPU, install NVIDIA's libraries: {pip_line()}"
    if reported == CUDA:
        return "the GPU (CUDA)"
    if reported == CPU:
        return "the CPU: the GPU didn't start, so speech fell back to it"
    return "the GPU, from the next use"


def prepare(*, dirs: Iterable[Path] | None = None, environ: MutableMapping[str, str] | None = None,
            add_dll_directory: Callable[[str], Any] | None = None) -> list[Path]:
    """In a speech child process, before faster-whisper loads: the libraries' folders go on this process's DLL search
    path (``os.add_dll_directory`` and the front of PATH). Returns the folders added."""
    folders = list(cuda_dirs() if dirs is None else dirs)
    env = os.environ if environ is None else environ
    add = add_dll_directory if add_dll_directory is not None else getattr(os, "add_dll_directory", None)
    for folder in folders:
        if add is not None:
            try:
                add(str(folder))
            except OSError:
                pass
    if folders:
        env["PATH"] = os.pathsep.join([*(str(folder) for folder in folders), env.get("PATH", "")])
    return folders


def cuda_devices() -> int:
    """How many CUDA devices ctranslate2 sees (0 when it can't say). Only ever called in a speech child process."""
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception:  # noqa: BLE001 - no CUDA: the CPU
        return 0


TEST_GUARD_ENV = "JARVIS_DESKTOP_TEST_GUARD"     # "1" while the tests run: the real GPU is never asked


def _gpu_ready() -> bool:
    if os.environ.get(TEST_GUARD_ENV, "") == "1":
        return False
    return libraries_found() and cuda_devices() > 0


def load_model(factory: Callable[..., Any], folder: Path, *, device: str = DEVICE_AUTO, threads: int = 4,
               warm: Callable[[Any], Any] | None = None, gpu_ready: Callable[[], bool] | None = None,
               **kwargs: Any) -> tuple[Any, str]:
    """(model, CUDA or CPU). ``factory`` is faster_whisper.WhisperModel. With ``device`` DEVICE_AUTO and the GPU ready
    (``gpu_ready``: the libraries found and a CUDA device seen) the model loads on the GPU in float16 and ``warm(model)``
    runs it once; any failure there - or DEVICE_CPU - loads it on the CPU in int8 with ``threads`` threads."""
    if device != DEVICE_CPU:
        try:
            if (gpu_ready or _gpu_ready)():
                model = factory(str(folder), device=CUDA, compute_type="float16", **kwargs)
                if warm is not None:
                    warm(model)
                return model, CUDA
        except Exception:  # noqa: BLE001 - no GPU after all: the CPU, as before (any model on it is let go)
            pass
    model = factory(str(folder), device=CPU, compute_type="int8", cpu_threads=max(1, int(threads)), **kwargs)
    return model, CPU


def warm_up(model: Any) -> None:
    """One short pass over half a second of quiet noise, without the voice filter (which would skip it), so every GPU
    library loads now rather than at the owner's first words."""
    import numpy as np

    noise = np.random.default_rng(7).normal(0.0, 0.003, 8000).astype(np.float32)
    segments, _info = model.transcribe(noise, language="en", beam_size=1, vad_filter=False, without_timestamps=True,
                                       condition_on_previous_text=False, max_new_tokens=4)
    for _segment in segments:
        pass
