"""Jarvis's voice, made on this PC: the child process (owner decisions 2026-11-12).

``python -m briefing_reader.local_voice_worker --model DIR --g2p-gb DIR --g2p-us DIR --out-root DIR [--threads 8]
[--idle-s 0] [--voice bm_george]`` loads once: Kokoro-82M (onnx-community's ONNX export with word timings) in
onnxruntime on the CPU, misaki's English word-to-sound (British for the b* voices, American for the a* ones; each
made when first needed) with our own unknown-word model from --g2p-gb / --g2p-us, and each voice as it is asked for.
--voice is warmed before "ready", so the first line needs no wait. The process ends on its own after --idle-s seconds
without a request (0: never), when its input closes, or on ``{"quit": true}``.

Protocol. stdin takes JSON lines ``{"id", "text", "voice", "speed", "gain", "out"}``; stdout answers JSON lines: first
``{"ready": true, "load_ms": N}`` (or ``{"ready": false, "error": CODE}``, and the process ends), then one per request:
``{"id", "ok": true, "ms", "duration_ms", "words": [[start_ms, word], ...]}`` or ``{"id", "ok": false, "error":
CODE}``. Each WAV (24 kHz mono 16-bit) goes to "out" as "<out>.part" and is then renamed; "out" must be a .wav inside
--out-root. A word's start comes from the model's own durations (600 samples a unit, scaled to the audio's length).

Privacy. The words travel only on these pipes. The process logs nothing, writes nothing else and never uses the
network: HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE are set before anything loads, the unknown-word model loads from our own
folder only (misaki's own would download it from Hugging Face), and spaCy's model download - which runs pip - is
refused (misaki calls it when en_core_web_sm is missing; the app names that add-on instead). What a library prints
goes to stderr (the app gives it none), never onto the answers' pipe.

Licences: Kokoro-82M and its voices, misaki and the unknown-word models are Apache-2.0; onnxruntime and spaCy MIT;
num2words LGPL-2.1 (used unchanged). No espeak-ng or phonemizer (GPL-3.0) is ever loaded.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Protocol

from .local_voice import (ERR_ADDON, ERR_FAILED, ERR_MODEL, ERR_PATH, ERR_TOO_LONG, ERR_VOICE, MAX_TEXT_CHARS,
                          MODEL_FILE, OFFLINE_ENV, SAMPLE_RATE, SAMPLE_WIDTH, THREADS, VOCAB_FILE, VOICE_NAMES,
                          british)

MAX_TOKENS = 510                       # Kokoro's longest input (phonemes, without the two pads)
SAMPLES_PER_UNIT = 600                 # one unit of the model's durations (25 ms at 24 kHz)
STYLE_SIZE = 256
PEAK = 0.98                            # a louder [voice] volume is held below clipping
_STOPS = frozenset(".!?;:,…—")   # a token after which a long input is best cut
_INPUTS = ("input_ids", "style", "speed")
_OUTPUTS = ("waveform", "durations")
WARM_TEXT = "Ready."


class AddonMissing(RuntimeError):
    pass


class ModelMissing(RuntimeError):
    pass


class Speaker(Protocol):
    def prepare(self, voice: str) -> None: ...

    def speak(self, text: str, voice: str, speed: float) -> tuple[Any, list[tuple[int, str]]]: ...


# --------------------------------------------------------------------------
# Pure parts
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Piece:
    """One of misaki's tokens as the model takes it: its sounds' ids (and a space's, when a space follows), the word it
    says ("" for punctuation or a word with no sound), and whether a long input may be cut after it."""

    ids: tuple[int, ...]
    word: str = ""
    stop: bool = False

    def __repr__(self) -> str:                    # never the word itself
        return f"Piece({len(self.ids)} ids, word={bool(self.word)}, stop={self.stop})"


def token_pieces(tokens: Sequence[Any], vocab: dict[str, int]) -> list[Piece]:
    """misaki's tokens (``.text``, ``.phonemes``, ``.whitespace``) as Pieces; a sound the model has no id for is
    dropped, as the model's own tokenizer drops it."""
    space = vocab.get(" ")
    pieces: list[Piece] = []
    for token in tokens:
        text = str(getattr(token, "text", "") or "")
        phonemes = getattr(token, "phonemes", None) or ""
        ids = [vocab[char] for char in phonemes if char in vocab]
        sounds = len(ids)
        if getattr(token, "whitespace", "") and space is not None:
            ids.append(space)
        word = text if sounds and any(char.isalnum() for char in text) else ""
        stop = bool(text) and all(char in _STOPS for char in text.strip())
        if ids:
            pieces.append(Piece(tuple(ids), word, stop))
    return pieces


def chunk_pieces(pieces: Sequence[Piece], limit: int = MAX_TOKENS) -> list[list[Piece]]:
    """The pieces in inputs of at most ``limit`` ids, cut after a clause's mark when one is in reach; a single piece
    longer than ``limit`` is cut to it."""
    chunks: list[list[Piece]] = []
    current: list[Piece] = []
    size = 0
    for piece in pieces:
        if len(piece.ids) > limit:
            piece = Piece(piece.ids[:limit], piece.word, piece.stop)
        while current and size + len(piece.ids) > limit:
            cut = max((index for index, item in enumerate(current) if item.stop), default=-1)
            if 0 <= cut < len(current) - 1:
                chunks.append(current[:cut + 1])
                current = current[cut + 1:]
            else:
                chunks.append(current)
                current = []
            size = sum(len(item.ids) for item in current)
        current.append(piece)
        size += len(piece.ids)
    if current:
        chunks.append(current)
    return chunks


def word_starts(pieces: Sequence[Piece], durations: Sequence[float], samples: int) -> list[tuple[int, str]]:
    """When each word of one input starts (ms), from the model's ``durations`` (one per input id, the two pads
    included: the first is the leading pad's), scaled so they add up to the ``samples`` the model made."""
    total = float(sum(durations))
    if total <= 0 or samples <= 0:
        return []
    scale = samples / (total * SAMPLES_PER_UNIT)
    starts: list[tuple[int, str]] = []
    running = float(durations[0]) if durations else 0.0      # the leading pad
    index = 1
    for piece in pieces:
        if piece.word:
            ms = running * SAMPLES_PER_UNIT * scale * 1000.0 / SAMPLE_RATE
            starts.append((max(0, round(ms)), piece.word))
        for _ in piece.ids:
            if index < len(durations):
                running += float(durations[index])
            index += 1
    return starts


def pcm16(samples: Any, gain: float = 1.0) -> bytes:
    """Float samples (-1..1) as little-endian 16-bit PCM, at ``gain``, held below clipping."""
    import numpy as np

    audio = np.asarray(samples, dtype=np.float32).ravel() * float(gain)
    peak = float(np.abs(audio).max()) if audio.size else 0.0
    if peak > PEAK:
        audio = audio * (PEAK / peak)
    return (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def write_wav(path: Path, pcm: bytes) -> None:
    """``pcm`` as a 24 kHz mono 16-bit WAV at ``path``, written as ``<path>.part`` and then renamed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        with wave.open(str(part), "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(SAMPLE_WIDTH)
            target.setframerate(SAMPLE_RATE)
            target.writeframes(pcm)
        os.replace(part, path)
    finally:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass


def inside(path: Path, root: Path) -> bool:
    """``path`` is a .wav inside ``root`` (both resolved)."""
    try:
        resolved, base = Path(path).resolve(), Path(root).resolve()
    except (OSError, ValueError):
        return False
    return resolved.suffix.lower() == ".wav" and base in resolved.parents


def refuse_spacy_download() -> None:
    """spaCy's model download runs pip: in this process it raises instead (misaki calls it when its model is missing)."""
    import spacy.cli

    def refused(*_args: Any, **_kwargs: Any) -> None:
        raise AddonMissing("en_core_web_sm")

    spacy.cli.download = refused


# --------------------------------------------------------------------------
# The real voice
# --------------------------------------------------------------------------

FALLBACK_WAIT_S = 60.0                 # a word that needs the unknown-word model waits this long for it to load


class WordFallback:
    """misaki's unknown-word step, from our own copy of PeterReid/graphemes_to_phonemes_en_* (Apache-2.0; a 3 MB BART):
    the same model misaki's FallbackNetwork downloads, loaded here from ``folder`` only, on the CPU. It loads on a
    thread of its own (importing transformers alone takes about 8 s here), so the voice is ready without it: only a word
    misaki doesn't know waits for it, and when it can't load such a word is left unsaid."""

    def __init__(self, folder: Path, *, loader: Callable[[Path], tuple[Any, Any, int]] | None = None) -> None:
        self._folder = Path(folder)
        self._loader = loader or self._load_bart
        self._ready = threading.Event()
        self._run: Callable[[list[int]], list[int]] | None = None
        self._graphemes: dict[str, int] = {}
        self._phonemes: dict[int, str] = {}
        self._longest = 62
        threading.Thread(target=self._load, name="word-fallback-load", daemon=True).start()

    @staticmethod
    def _load_bart(folder: Path) -> tuple[Any, Callable[[list[int]], list[int]], int]:
        import torch
        from transformers import BartForConditionalGeneration

        model = BartForConditionalGeneration.from_pretrained(str(folder), local_files_only=True)
        model.eval()

        def run(ids: list[int]) -> list[int]:
            with torch.no_grad():
                return model.generate(input_ids=torch.tensor([ids]))[0].tolist()

        return model.config, run, max(1, int(getattr(model.config, "max_position_embeddings", 64)) - 2)

    def _load(self) -> None:
        try:
            config, self._run, self._longest = self._loader(self._folder)
            self._graphemes = {char: index for index, char in enumerate(config.grapheme_chars)}
            self._phonemes = {index: char for index, char in enumerate(config.phoneme_chars)}
        except Exception:  # noqa: BLE001 - no unknown-word model: such words are left unsaid
            self._run = None
        finally:
            self._ready.set()

    def __call__(self, token: Any) -> tuple[str, int]:
        text = str(getattr(token, "text", "") or "")
        if not text or not self._ready.wait(FALLBACK_WAIT_S) or self._run is None:
            return "", 1
        ids = [1] + [self._graphemes.get(char, 3) for char in text[: self._longest]] + [2]
        return "".join(self._phonemes.get(index, "") for index in self._run(ids) if index > 3), 1


def make_g2p(is_british: bool, folder: Path) -> Any:
    """misaki's English word-to-sound with our unknown-word model; AddonMissing when spaCy's English model is not
    installed (never downloaded here)."""
    refuse_spacy_download()
    import spacy.util

    if not spacy.util.is_package("en_core_web_sm"):
        raise AddonMissing("en_core_web_sm")
    from misaki import en

    return en.G2P(trf=False, british=is_british, fallback=WordFallback(folder), unk="")


class KokoroSpeaker:
    """Kokoro-82M on the CPU: text -> (float samples at 24 kHz, [(start_ms, word)]). ``session``, ``vocab``,
    ``make_g2p(british)`` and ``voice_pack(name)`` are given (``load`` makes the real ones; tests give fakes)."""

    def __init__(self, session: Any, vocab: dict[str, int], make_g2p: Callable[[bool], Any],
                 voice_pack: Callable[[str], Any]) -> None:
        import numpy as np

        self._np = np
        self._session = session
        self._vocab = dict(vocab)
        self._make_g2p = make_g2p
        self._voice_pack = voice_pack
        self._g2ps: dict[bool, Any] = {}
        self._packs: dict[str, Any] = {}

    @classmethod
    def load(cls, model_dir: Path, g2p_dirs: dict[bool, Path], threads: int = THREADS) -> KokoroSpeaker:
        model_dir = Path(model_dir)
        model, vocab_file, voices = model_dir / MODEL_FILE, model_dir / VOCAB_FILE, model_dir / "voices"
        if not (model.is_file() and vocab_file.is_file() and voices.is_dir()
                and all(Path(folder).is_dir() for folder in g2p_dirs.values())):
            raise ModelMissing(model_dir.name)
        import numpy as np
        import onnxruntime as ort

        vocab = json.loads(vocab_file.read_text(encoding="utf-8"))["model"]["vocab"]
        disable = getattr(ort, "disable_telemetry_events", None)
        if callable(disable):                    # onnxruntime's own usage events (Windows builds): off
            disable()
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, int(threads))
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        session = ort.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"])
        names = {item.name for item in session.get_inputs()}, {item.name for item in session.get_outputs()}
        if not (set(_INPUTS) <= names[0] and set(_OUTPUTS) <= names[1]):
            raise ModelMissing("not the timestamped Kokoro export")

        def pack(name: str) -> Any:
            return np.fromfile(voices / f"{name}.bin", dtype=np.float32).reshape(-1, 1, STYLE_SIZE)

        return cls(session, vocab, lambda is_british: make_g2p(is_british, g2p_dirs[is_british]), pack)

    def _g2p(self, is_british: bool) -> Any:
        if is_british not in self._g2ps:
            self._g2ps[is_british] = self._make_g2p(is_british)
        return self._g2ps[is_british]

    def _pack(self, voice: str) -> Any:
        if voice not in self._packs:
            self._packs[voice] = self._voice_pack(voice)
        return self._packs[voice]

    def prepare(self, voice: str) -> None:
        """The voice's word-to-sound, its file and the model's first run, before the first real line."""
        self.speak(WARM_TEXT, voice, 1.0)

    def _run(self, ids: list[int], pack: Any, speed: float) -> tuple[Any, list[float]]:
        np = self._np
        style = np.asarray(pack[min(len(ids), len(pack)) - 1], dtype=np.float32).reshape(1, STYLE_SIZE)
        feeds = {"input_ids": np.array([[0, *ids, 0]], dtype=np.int64), "style": style,
                 "speed": np.array([speed], dtype=np.float32)}
        wave_out, durations = self._session.run(list(_OUTPUTS), feeds)
        return (np.asarray(wave_out, dtype=np.float32).ravel(),
                [float(value) for value in np.asarray(durations).ravel()])

    def speak(self, text: str, voice: str, speed: float) -> tuple[Any, list[tuple[int, str]]]:
        np = self._np
        g2p, pack = self._g2p(british(voice)), self._pack(voice)
        _phonemes, tokens = g2p(" ".join(str(text).split()))
        audio: list[Any] = []
        words: list[tuple[int, str]] = []
        offset = 0
        for chunk in chunk_pieces(token_pieces(tokens or (), self._vocab)):
            ids = [index for piece in chunk for index in piece.ids]
            if not ids or not any(piece.word for piece in chunk):
                continue                           # only marks and spaces: nothing to say
            samples, durations = self._run(ids, pack, speed)
            base = round(offset * 1000 / SAMPLE_RATE)
            words.extend((base + ms, word) for ms, word in word_starts(chunk, durations, len(samples)))
            audio.append(samples)
            offset += len(samples)
        if not audio:                              # nothing to say (only marks): a tenth of a second of quiet
            return np.zeros(SAMPLE_RATE // 10, dtype=np.float32), []
        return np.concatenate(audio), words


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------

_EOF = object()


def _write(out: IO[str], payload: dict) -> None:
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


def handle(request: dict, speaker: Speaker, out_root: Path, clock: Callable[[], float] = time.monotonic) -> dict:
    """One request's answer (see the module docstring)."""
    rid = request.get("id")
    text, voice, out = request.get("text"), request.get("voice"), request.get("out")
    if not isinstance(text, str) or not isinstance(out, str):
        return {"id": rid, "ok": False, "error": ERR_FAILED}
    if len(text) > MAX_TEXT_CHARS:
        return {"id": rid, "ok": False, "error": ERR_TOO_LONG}
    if voice not in VOICE_NAMES:
        return {"id": rid, "ok": False, "error": ERR_VOICE}
    path = Path(out)
    if not inside(path, out_root):
        return {"id": rid, "ok": False, "error": ERR_PATH}
    speed = _number(request.get("speed"), 1.0, 0.5, 2.0)
    gain = _number(request.get("gain"), 1.0, 0.0, 2.0)
    began = clock()
    try:
        samples, words = speaker.speak(text, voice, speed)
        pcm = pcm16(samples, gain)
        write_wav(path, pcm)
    except Exception:  # noqa: BLE001 - one line that fails never ends the process; its code only, never its text
        return {"id": rid, "ok": False, "error": ERR_FAILED}
    frames = len(pcm) // SAMPLE_WIDTH
    return {"id": rid, "ok": True, "ms": int((clock() - began) * 1000),
            "duration_ms": round(frames * 1000 / SAMPLE_RATE),
            "words": [[int(ms), str(word)] for ms, word in words]}


def serve(inp: IO[bytes], out: IO[str], load: Callable[[], Speaker], *, out_root: Path, idle_s: float = 0.0,
          warm_voice: str = "", clock: Callable[[], float] = time.monotonic) -> int:
    """The process's loop (see the module docstring); returns its exit code."""
    began = clock()
    try:
        speaker = load()
        if warm_voice in VOICE_NAMES:
            speaker.prepare(warm_voice)
    except (AddonMissing, ImportError):
        _write(out, {"ready": False, "error": ERR_ADDON})
        return 2
    except ModelMissing:
        _write(out, {"ready": False, "error": ERR_MODEL})
        return 2
    except Exception:  # noqa: BLE001 - any failure to load is reported by its code, never its text
        _write(out, {"ready": False, "error": ERR_FAILED})
        return 2
    _write(out, {"ready": True, "load_ms": int((clock() - began) * 1000)})
    requests: queue.Queue = queue.Queue()
    threading.Thread(target=_reader, args=(inp, requests), name="local-voice-in", daemon=True).start()
    while True:
        try:
            request = requests.get(timeout=idle_s) if idle_s > 0 else requests.get()
        except queue.Empty:
            return 0                                      # idle: the next line starts the process again
        if request is _EOF or request.get("quit"):
            return 0
        _write(out, handle(request, speaker, out_root, clock))


def answer_stream() -> IO[str]:
    """The answers' pipe, kept for them alone: a copy of descriptor 1 (under pythonw sys.stdout may be None even with a
    pipe attached), with descriptor 1 itself - and Python's sys.stdout - pointed at the null device, so nothing a
    library prints, from Python or from native code, can land between two answers."""
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
    parser = argparse.ArgumentParser(prog="briefing_reader.local_voice_worker")
    parser.add_argument("--model", required=True)
    parser.add_argument("--g2p-gb", required=True)
    parser.add_argument("--g2p-us", required=True)
    parser.add_argument("--out-root", required=True)
    parser.add_argument("--threads", type=int, default=THREADS)
    parser.add_argument("--idle-s", type=float, default=0.0)
    parser.add_argument("--voice", default="")
    args = parser.parse_args(argv)
    out = answer_stream()
    inp = open(0, "rb", closefd=False)  # noqa: SIM115
    dirs = {True: Path(args.g2p_gb), False: Path(args.g2p_us)}
    return serve(inp, out, lambda: KokoroSpeaker.load(Path(args.model), dirs, args.threads),
                 out_root=Path(args.out_root), idle_s=max(0.0, args.idle_s), warm_voice=args.voice)


if __name__ == "__main__":
    sys.exit(main())
