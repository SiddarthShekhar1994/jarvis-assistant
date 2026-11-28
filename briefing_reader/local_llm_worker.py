"""Answers made on this PC by a simpler model: the child process (owner decision 2026-11-28).

``python -m briefing_reader.local_llm_worker --model DIR [--idle-s 300] [--threads 8]`` loads once: the model in DIR
(Phi-3.5-mini-instruct converted to CTranslate2) with ctranslate2's Generator - on the graphics card as int8_float16
first, on the processor as int8 when the card can't take it - and its tokenizer.json with the ``tokenizers`` package.
The process ends on its own after --idle-s seconds without a request (0: never), when its input closes, or on
``{"quit": true}``: its memory then goes back to the voice models.

Protocol. stdin takes JSON lines ``{"id", "prompt", "max_tokens", "timeout_s"}`` (the prompt in the model's chat format,
local_llm.build_prompt); stdout answers JSON lines: first ``{"ready": true, "load_ms": N, "device": "cuda" | "cpu"}``
(or ``{"ready": false, "error": CODE}``, and the process ends), then one per request: ``{"id", "ok": true, "text",
"prompt_tokens", "output_tokens", "ms", "stopped": "end" | "max_tokens"}`` or ``{"id", "ok": false, "error": CODE}``.
Greedy decoding; it stops at <|end|>, <|endoftext|> or <|assistant|>, at max_tokens, and at timeout_s (ERR_TIMEOUT).

Privacy. The words travel only on these pipes. The process logs nothing, writes nothing and never uses the network:
HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE are set before anything loads, and the model and tokenizer load from DIR only.
What a library prints goes to stderr (the app gives it none), never onto the answers' pipe.

Licence: the model is Phi-3.5-mini-instruct by Microsoft (MIT); ctranslate2 and tokenizers are MIT and Apache-2.0.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Protocol

from .local_llm import (CPU_COMPUTE, DEVICE_CPU, DEVICE_GPU, END_TOKENS, ERR_ADDON, ERR_FAILED, ERR_MODEL,
                        ERR_TIMEOUT, ERR_TOO_LONG, GPU_COMPUTE, OFFLINE_ENV, missing_files)

MAX_PROMPT_TOKENS = 6_000              # far above what local_llm.build_messages makes (about 3,000 at most)
MAX_NEW_TOKENS = 1_024                 # [local_model] max_tokens' own top
REPETITION_PENALTY = 1.05
THREADS = 8
_EOF = object()
_write_lock = threading.Lock()


class AddonMissing(RuntimeError):
    pass


class ModelMissing(RuntimeError):
    pass


class TooLong(ValueError):
    pass


@dataclass(frozen=True)
class Made:
    """One answer as made (the words, the tokens in and out, why it stopped: "end", "max_tokens" or "timeout")."""

    text: str
    prompt_tokens: int
    output_tokens: int
    stopped: str


class Engine(Protocol):
    device: str

    def generate(self, prompt: str, max_tokens: int, deadline: float, clock: Callable[[], float]) -> Made: ...


class Ct2Engine:
    """The model in ctranslate2 and its tokenizer (only ``load`` imports them)."""

    def __init__(self, generator: Any, tokenizer: Any, device: str) -> None:
        self.generator = generator
        self.tokenizer = tokenizer
        self.device = device
        self._end_ids = {tokenizer.token_to_id(token) for token in END_TOKENS} - {None}

    @classmethod
    def load(cls, folder: Path, threads: int = THREADS) -> Ct2Engine:
        if missing_files(folder):
            raise ModelMissing()
        try:
            import ctranslate2
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise AddonMissing() from exc
        tokenizer = Tokenizer.from_file(str(Path(folder) / "tokenizer.json"))
        generator, device = None, DEVICE_CPU
        try:
            if ctranslate2.get_cuda_device_count() > 0:
                generator = ctranslate2.Generator(str(folder), device=DEVICE_GPU, compute_type=GPU_COMPUTE)
                device = DEVICE_GPU
        except Exception:  # noqa: BLE001 - no card, no room on it, or its libraries missing: the processor
            generator = None
        if generator is None:
            generator = ctranslate2.Generator(str(folder), device=DEVICE_CPU, compute_type=CPU_COMPUTE,
                                              intra_threads=max(1, int(threads)))
            device = DEVICE_CPU
        return cls(generator, tokenizer, device)

    def generate(self, prompt: str, max_tokens: int, deadline: float, clock: Callable[[], float]) -> Made:
        tokens = self.tokenizer.encode(prompt, add_special_tokens=False).tokens
        if len(tokens) > MAX_PROMPT_TOKENS:
            raise TooLong()
        ids: list[int] = []
        stopped = "end"
        steps = self.generator.generate_tokens(tokens, max_length=len(tokens) + max_tokens, sampling_topk=1,
                                               repetition_penalty=REPETITION_PENALTY, end_token=list(END_TOKENS))
        try:
            for step in steps:
                if step.token_id in self._end_ids:
                    break
                ids.append(step.token_id)
                if len(ids) >= max_tokens:
                    stopped = "max_tokens"
                    break
                if clock() > deadline:
                    stopped = ERR_TIMEOUT
                    break
        finally:
            close = getattr(steps, "close", None)
            if callable(close):
                close()                                   # stops the generation where it is
        return Made(self.tokenizer.decode(ids, skip_special_tokens=True), len(tokens), len(ids), stopped)


def _write(out: IO[str], payload: dict) -> None:
    with _write_lock:
        out.write(json.dumps(payload, ensure_ascii=False) + "\n")
        out.flush()


def _reader(stream: IO[bytes], requests: queue.Queue) -> None:
    """JSON lines from ``stream`` into ``requests`` (a line that is not one is skipped); _EOF when it ends."""
    try:
        for raw in iter(stream.readline, b""):
            try:
                request = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(request, dict):
                requests.put(request)
    except (OSError, ValueError):
        pass
    requests.put(_EOF)


def _number(value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, low), high) if number == number else default


def handle(request: dict, engine: Engine, clock: Callable[[], float] = time.monotonic) -> dict:
    """One request's answer (see the module docstring)."""
    rid = request.get("id")
    prompt = request.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        return {"id": rid, "ok": False, "error": ERR_FAILED}
    max_tokens = int(_number(request.get("max_tokens"), 512, 1, MAX_NEW_TOKENS))
    timeout_s = _number(request.get("timeout_s"), 90.0, 1.0, 600.0)
    began = clock()
    try:
        made = engine.generate(prompt, max_tokens, began + timeout_s, clock)
    except TooLong:
        return {"id": rid, "ok": False, "error": ERR_TOO_LONG}
    except Exception:  # noqa: BLE001 - one answer that fails never ends the process; its code only, never its text
        return {"id": rid, "ok": False, "error": ERR_FAILED}
    if made.stopped == ERR_TIMEOUT:
        return {"id": rid, "ok": False, "error": ERR_TIMEOUT}
    return {"id": rid, "ok": True, "text": made.text, "prompt_tokens": made.prompt_tokens,
            "output_tokens": made.output_tokens, "ms": int((clock() - began) * 1000), "stopped": made.stopped}


def serve(inp: IO[bytes], out: IO[str], load: Callable[[], Engine], *, idle_s: float = 0.0,
          clock: Callable[[], float] = time.monotonic) -> int:
    """The process's loop (see the module docstring); returns its exit code."""
    began = clock()
    try:
        engine = load()
    except (AddonMissing, ImportError):
        _write(out, {"ready": False, "error": ERR_ADDON})
        return 2
    except ModelMissing:
        _write(out, {"ready": False, "error": ERR_MODEL})
        return 2
    except Exception:  # noqa: BLE001 - any failure to load is reported by its code, never its text
        _write(out, {"ready": False, "error": ERR_FAILED})
        return 2
    _write(out, {"ready": True, "load_ms": int((clock() - began) * 1000), "device": getattr(engine, "device", "")})
    requests: queue.Queue = queue.Queue()
    threading.Thread(target=_reader, args=(inp, requests), name="local-model-in", daemon=True).start()
    while True:
        try:
            request = requests.get(timeout=idle_s) if idle_s > 0 else requests.get()
        except queue.Empty:
            return 0                                      # idle: the next answer starts the process again
        if request is _EOF or request.get("quit"):
            return 0
        _write(out, handle(request, engine, clock))


def answer_stream() -> IO[str]:
    """The answers' pipe, kept for them alone: a copy of descriptor 1, with descriptor 1 itself - and sys.stdout -
    pointed at the null device, so nothing a library prints can land between two answers (local_voice_worker's)."""
    answers = os.dup(1)
    try:
        null = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(null, 1)
        finally:
            os.close(null)
    except OSError:
        pass
    sys.stdout = sys.stderr if sys.stderr is not None else open(os.devnull, "w")  # noqa: SIM115
    return open(answers, "w", encoding="utf-8", newline="\n")  # noqa: SIM115


def main(argv: list[str] | None = None) -> int:
    os.environ.update(OFFLINE_ENV)                        # before anything that could reach the network loads
    parser = argparse.ArgumentParser(prog="briefing_reader.local_llm_worker")
    parser.add_argument("--model", required=True)
    parser.add_argument("--idle-s", type=float, default=0.0)
    parser.add_argument("--threads", type=int, default=THREADS)
    args = parser.parse_args(argv)
    out = answer_stream()
    inp = open(0, "rb", closefd=False)  # noqa: SIM115
    return serve(inp, out, lambda: Ct2Engine.load(Path(args.model), args.threads), idle_s=max(0.0, args.idle_s))


if __name__ == "__main__":
    sys.exit(main())
