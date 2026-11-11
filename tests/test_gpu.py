"""Speech on an NVIDIA GPU (briefing_reader.gpu and its use in talk_speech, meeting_speech, talk_ui and meetings_ui):
finding NVIDIA's libraries, Settings' words, loading on the GPU with the CPU as the fallback (a load or warm-up that
fails), the device passed to the speech processes and the one they report. Fake folders, fake models and fake
processes: the real GPU, ctranslate2's CUDA and the network are never touched."""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from briefing_reader import config, gpu, meeting_speech, talk_speech


class Folders(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    def wheels(self, *, cublas: bool = True, cudnn: bool = True, cudnn_ops: bool = True) -> Path:
        nvidia = self.root / "site-packages" / "nvidia"
        for wanted, part, names in ((cublas, "cublas", ("cublas64_12.dll", "cublasLt64_12.dll")),
                                    (cudnn, "cudnn", ("cudnn64_9.dll", "cudnn_cnn64_9.dll")),
                                    (cudnn_ops, "cudnn", ("cudnn_ops64_9.dll",))):
            folder = nvidia / part / "bin"
            folder.mkdir(parents=True, exist_ok=True)
            for name in names:
                if wanted:
                    (folder / name).write_bytes(b"MZ")
        return nvidia


class LibraryTests(Folders):
    def test_the_wheels_folders_and_their_libraries(self) -> None:
        nvidia = self.wheels()
        dirs = gpu.cuda_dirs([nvidia])
        self.assertEqual(dirs, [nvidia / "cublas" / "bin", nvidia / "cudnn" / "bin"])
        self.assertTrue(gpu.libraries_found(dirs, path_env=""))
        self.assertFalse(gpu.libraries_found(gpu.cuda_dirs([self.wheels(cudnn=False).parent / "other"]), path_env=""))

    def test_a_missing_library_and_one_found_on_path(self) -> None:
        nvidia = self.wheels(cudnn=False)
        dirs = gpu.cuda_dirs([nvidia])
        self.assertFalse(gpu.libraries_found(dirs, path_env=""))
        elsewhere = self.root / "cuda" / "bin"
        elsewhere.mkdir(parents=True)
        for name in ("cudnn64_9.dll", "cudnn_cnn64_9.dll"):
            (elsewhere / name).write_bytes(b"MZ")
        self.assertTrue(gpu.libraries_found(dirs, path_env=str(elsewhere)))

    def test_a_part_of_cudnn_missing_is_no_gpu(self) -> None:
        self.assertFalse(gpu.libraries_found(gpu.cuda_dirs([self.wheels(cudnn_ops=False)]), path_env=""))

    def test_the_driver_and_the_status(self) -> None:
        system32 = self.root / "System32"
        system32.mkdir()
        self.assertFalse(gpu.driver_found(system32))
        (system32 / "nvcuda.dll").write_bytes(b"MZ")
        self.assertTrue(gpu.driver_found(system32))
        yes, no = (lambda: True), (lambda: False)
        self.assertEqual(gpu.status("cpu", driver=yes, libraries=yes), gpu.CPU_CHOSEN)
        self.assertEqual(gpu.status("auto", driver=no, libraries=yes), gpu.NO_GPU)
        self.assertEqual(gpu.status("auto", driver=yes, libraries=no), gpu.ADDONS_MISSING)
        self.assertEqual(gpu.status("auto", driver=yes, libraries=yes), gpu.READY)

    def test_prepare_puts_the_folders_first_on_the_search_path(self) -> None:
        nvidia = self.wheels()
        env = {"PATH": r"C:\Windows"}
        added: list[str] = []
        folders = gpu.prepare(dirs=gpu.cuda_dirs([nvidia]), environ=env, add_dll_directory=added.append)
        self.assertEqual([Path(item) for item in added], folders)
        self.assertEqual(env["PATH"].split(os.pathsep), [*map(str, folders), r"C:\Windows"])
        untouched = {"PATH": "x"}
        self.assertEqual(gpu.prepare(dirs=[], environ=untouched, add_dll_directory=added.append), [])
        self.assertEqual(untouched, {"PATH": "x"})

    def test_settings_words_and_the_pip_line(self) -> None:
        line = gpu.pip_line(r"C:\Python313\pythonw.exe")
        self.assertEqual(line, 'C:/Python313/python.exe -m pip install "nvidia-cublas-cu12==12.*" '
                               '"nvidia-cudnn-cu12==9.*"')
        self.assertTrue(gpu.pip_line(r"C:\Program Files\Python\python.exe").startswith('& "C:/Program Files'))
        self.assertEqual(gpu.runs_on_text(gpu.READY, gpu.CUDA), "the GPU (CUDA)")
        self.assertEqual(gpu.runs_on_text(gpu.READY), "the GPU, from the next use")
        self.assertIn("fell back", gpu.runs_on_text(gpu.READY, gpu.CPU))
        self.assertIn("pip install", gpu.runs_on_text(gpu.ADDONS_MISSING))
        self.assertEqual(gpu.runs_on_text(gpu.NO_GPU), "the CPU (no NVIDIA GPU or driver found)")
        self.assertEqual(gpu.runs_on_text(gpu.CPU_CHOSEN, setting="[talk] device"),
                         'the CPU ([talk] device = "cpu" in config.toml)')

    def test_the_real_gpu_is_never_asked_while_the_tests_run(self) -> None:
        self.assertEqual(os.environ.get(gpu.TEST_GUARD_ENV), "1")
        with mock.patch.object(gpu, "cuda_devices", side_effect=AssertionError("asked")):
            self.assertFalse(gpu._gpu_ready())


class FakeModel:
    """faster_whisper.WhisperModel's stand-in: records how it was made; ``fail_on`` makes loading or the first
    transcription on that device fail."""

    made: list[tuple[str, str]] = []

    def __init__(self, folder: str, *, device: str, compute_type: str, cpu_threads: int = 0, fail_on: str = "",
                 fail_warm: bool = False, **_kwargs: object) -> None:
        self.device, self.fail_warm = device, fail_warm
        FakeModel.made.append((device, compute_type))
        if fail_on == device:
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")

    def transcribe(self, _audio: object, **kwargs: object):
        if self.fail_warm and self.device == gpu.CUDA:
            raise RuntimeError("Could not load library cudnn_ops64_9.dll")
        return iter([SimpleNamespace(start=0.0, end=0.5, text=" hello")]), None


class LoadTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeModel.made = []

    def load(self, device: str = gpu.DEVICE_AUTO, ready: bool = True, **fake: object):
        return gpu.load_model(lambda folder, **kwargs: FakeModel(folder, **kwargs, **fake), Path("m"), device=device,
                              threads=3, warm=gpu.warm_up, gpu_ready=lambda: ready)

    def test_the_gpu_in_float16_when_it_is_ready(self) -> None:
        model, device = self.load()
        self.assertEqual((device, model.device, FakeModel.made), (gpu.CUDA, gpu.CUDA, [("cuda", "float16")]))

    def test_any_failure_on_the_gpu_falls_back_to_the_cpu(self) -> None:
        for fake in ({"fail_on": "cuda"}, {"fail_warm": True}):
            with self.subTest(fake=fake):
                FakeModel.made = []
                _model, device = self.load(**fake)
                self.assertEqual(device, gpu.CPU)
                self.assertEqual(FakeModel.made, [("cuda", "float16"), ("cpu", "int8")])

    def test_the_cpu_when_chosen_or_not_ready(self) -> None:
        for device, ready in ((gpu.DEVICE_CPU, True), (gpu.DEVICE_AUTO, False)):
            with self.subTest(device=device, ready=ready):
                FakeModel.made = []
                self.assertEqual(self.load(device, ready)[1], gpu.CPU)
                self.assertEqual(FakeModel.made, [("cpu", "int8")])


class FakeProc:
    def __init__(self, lines: list[dict]) -> None:
        self.stdout = io.BytesIO(b"".join(json.dumps(line).encode() + b"\n" for line in lines))
        self.stdin = io.BytesIO()

    def poll(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        pass


class ProcessTests(unittest.TestCase):
    def test_talk_passes_the_device_and_keeps_the_one_reported(self) -> None:
        commands: list[list[str]] = []
        client = talk_speech.SpeechClient(Path("m"), device="cpu",
                                          spawn=lambda command: commands.append(command) or FakeProc(
                                              [{"ready": True, "load_ms": 7, "device": "cuda"}]))
        self.assertEqual(client._start(), ("", 7))
        self.assertEqual(commands[0][-2:], ["--device", "cpu"])
        self.assertEqual(client.device, gpu.CUDA)
        self.assertEqual(talk_speech.SpeechClient(Path("m"), device="bogus")._command()[-1], "auto")

    def test_the_talk_process_says_where_it_runs(self) -> None:
        out = io.StringIO()
        code = talk_speech.serve(io.BytesIO(b"\0\0\0\0"), out, lambda: SimpleNamespace(device="cuda"), idle_s=1)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue().splitlines()[0])["device"], "cuda")
        parser_args = ["--model", "m", "--device", "cpu"]
        with mock.patch.object(talk_speech, "serve", side_effect=lambda _i, _o, load, idle_s: load() and 0), \
                mock.patch.object(talk_speech, "FasterWhisperTalk") as talk:
            talk_speech.main(parser_args)
        self.assertEqual(talk.call_args.kwargs["device"], "cpu")

    def test_a_meeting_passes_the_device_and_reports_it(self) -> None:
        process = meeting_speech.TranscriptionProcess(Path("meeting"), Path("model"), on_progress=lambda _p: None,
                                                      on_done=lambda _e: None, device="cpu")
        with mock.patch.object(meeting_speech.subprocess, "Popen") as popen, \
                mock.patch.object(meeting_speech.threading, "Thread"):
            process.start()
        self.assertEqual(popen.call_args.args[0][-2:], ["--device", "cpu"])
        done: list[str] = []
        reader = meeting_speech.TranscriptionProcess(Path("meeting"), Path("model"), on_progress=lambda _p: None,
                                                     on_done=done.append)
        reader._proc = FakeProc([{"device": "cuda"}, {"progress": 50}, {"done": True}])
        with self.assertLogs("briefing_reader.meeting_speech", level="INFO") as logs:
            reader._read()
        self.assertEqual((reader.device, meeting_speech.last_device(), done), (gpu.CUDA, gpu.CUDA, [""]))
        self.assertIn("writing down on the GPU", "\n".join(logs.output))

    def test_the_meeting_worker_emits_the_device(self) -> None:
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(folder, ignore_errors=True))
        emitted: list[dict] = []
        with mock.patch.object(meeting_speech, "speech_missing", return_value=""), \
                mock.patch.object(meeting_speech, "model_ready", return_value=True), \
                mock.patch.object(meeting_speech, "FasterWhisperTranscriber",
                                  return_value=SimpleNamespace(device="cpu")) as made, \
                mock.patch.object(meeting_speech, "transcribe_meeting"), \
                mock.patch.object(meeting_speech, "_emit", side_effect=emitted.append):
            self.assertEqual(meeting_speech.main(["--meeting", str(folder), "--model", str(folder), "--device", "cpu"]),
                             0)
        self.assertEqual(made.call_args.kwargs["device"], "cpu")
        self.assertEqual(emitted, [{"device": "cpu"}, {"done": True}])

    def test_the_transcriber_uses_the_gpu_when_ready(self) -> None:
        FakeModel.made = []
        with mock.patch.object(meeting_speech.importlib, "import_module",
                               return_value=SimpleNamespace(WhisperModel=FakeModel)), \
                mock.patch.object(gpu, "_gpu_ready", return_value=True), \
                mock.patch.object(gpu, "prepare", return_value=[]) as prepared:
            transcriber = meeting_speech.FasterWhisperTranscriber(Path("m"))
        self.assertEqual(transcriber.device, gpu.CUDA)
        prepared.assert_called_once()
        with mock.patch.object(meeting_speech.importlib, "import_module",
                               return_value=SimpleNamespace(WhisperModel=FakeModel)), \
                mock.patch.object(gpu, "prepare") as prepared:
            self.assertEqual(meeting_speech.FasterWhisperTranscriber(Path("m"), device="cpu").device, gpu.CPU)
        prepared.assert_not_called()                     # "cpu": NVIDIA's libraries are never even looked for


class FallbackTests(unittest.TestCase):
    """Every way the GPU can fail ends on the CPU: a clip that fails, a process that crashes, hangs or never gets
    ready, a meeting worker that dies or fails half way."""

    def test_a_clip_that_fails_on_the_gpu_is_understood_again_on_the_cpu(self) -> None:
        class Talk:
            device = gpu.CUDA

            def transcribe(self, _clip: bytes) -> str:
                if self.device == gpu.CUDA:
                    raise RuntimeError("CUDA failed with error out of memory")
                return "what time is it"

            def to_cpu(self) -> None:
                self.device = gpu.CPU

        out = io.StringIO()
        clip = b"\x01\x00" * 400
        talk_speech.serve(io.BytesIO(len(clip).to_bytes(4, "little") + clip + b"\0\0\0\0"), out, Talk, idle_s=1)
        answer = json.loads(out.getvalue().splitlines()[1])
        self.assertEqual((answer["ok"], answer["text"], answer["device"]), (True, "what time is it", "cpu"))
        cpu_only = SimpleNamespace(device=gpu.CPU, transcribe=mock.Mock(side_effect=RuntimeError("x")))
        out = io.StringIO()
        talk_speech.serve(io.BytesIO(len(clip).to_bytes(4, "little") + clip + b"\0\0\0\0"), out, lambda: cpu_only,
                          idle_s=1)
        self.assertEqual(json.loads(out.getvalue().splitlines()[1]), {"ok": False, "error": talk_speech.ERR_FAILED})

    def test_a_talk_process_that_never_gets_ready_starts_again_on_the_cpu(self) -> None:
        commands: list[list[str]] = []
        replies = iter([FakeProc([]), FakeProc([{"ready": True, "load_ms": 9, "device": "cpu"}])])
        client = talk_speech.SpeechClient(Path("m"), spawn=lambda command: commands.append(command) or next(replies))
        self.assertEqual(client._start(), ("", 9))
        self.assertEqual([command[-1] for command in commands], ["auto", "cpu"])
        self.assertEqual(client.device, gpu.CPU)

    def test_a_crash_or_a_hang_on_the_gpu_keeps_the_cpu_for_the_run(self) -> None:
        client = talk_speech.SpeechClient(Path("m"))
        client.device = gpu.CUDA
        client._gpu_gave_out(0)                                  # it went idle: nothing changes
        self.assertEqual(client._command()[-1], "auto")
        client._gpu_gave_out(3221226505)                         # crashed
        self.assertEqual(client._command()[-1], "cpu")
        hung = talk_speech.SpeechClient(Path("m"))
        hung.device = gpu.CUDA
        hung._gpu_gave_out(None)
        self.assertEqual(hung._command()[-1], "cpu")

    def test_a_meeting_worker_that_dies_without_a_word_runs_again_on_the_cpu(self) -> None:
        meeting_speech._last_device.pop("fell_back", None)
        self.addCleanup(meeting_speech._last_device.pop, "fell_back", None)
        done: list[str] = []
        process = meeting_speech.TranscriptionProcess(Path("meeting"), Path("model"), on_progress=lambda _p: None,
                                                      on_done=done.append)
        process._proc = FakeProc([{"device": "cuda"}, {"progress": 30}])
        with mock.patch.object(process, "start") as started, self.assertLogs("briefing_reader.meeting_speech",
                                                                             level="INFO"):
            process._read()
        started.assert_called_once_with()
        self.assertEqual((process.device_setting, done), (gpu.DEVICE_CPU, []))
        later = meeting_speech.TranscriptionProcess(Path("m2"), Path("model"), on_progress=lambda _p: None,
                                                    on_done=lambda _e: None)
        self.assertEqual(later.device_setting, gpu.DEVICE_CPU)   # the rest of this run stays on the CPU

    def test_a_meeting_that_fails_half_way_on_the_gpu_is_done_again_on_the_cpu(self) -> None:
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(folder, ignore_errors=True))
        emitted: list[dict] = []
        made = [SimpleNamespace(device="cuda"), SimpleNamespace(device="cpu")]
        runs = iter([RuntimeError("CUDA error: out of memory"), None])

        def transcribe(_folder, _transcriber, _progress):
            failure = next(runs)
            if failure is not None:
                raise failure

        with mock.patch.object(meeting_speech, "speech_missing", return_value=""), \
                mock.patch.object(meeting_speech, "model_ready", return_value=True), \
                mock.patch.object(meeting_speech, "FasterWhisperTranscriber", side_effect=made) as transcriber, \
                mock.patch.object(meeting_speech, "transcribe_meeting", side_effect=transcribe), \
                mock.patch.object(meeting_speech, "_emit", side_effect=emitted.append):
            self.assertEqual(meeting_speech.main(["--meeting", str(folder), "--model", str(folder)]), 0)
        self.assertEqual(transcriber.call_args_list[1].kwargs["device"], "cpu")
        self.assertEqual(emitted, [{"device": "cuda"}, {"device": "cpu"}, {"done": True}])


class ConfigTests(unittest.TestCase):
    def test_the_device_settings_and_the_new_model(self) -> None:
        self.assertIn("distil-large-v3", config.TALK_MODELS)
        self.assertIn("distil-large-v3", meeting_speech.SPEECH_MODELS)
        self.assertEqual(meeting_speech.SPEECH_MODELS["distil-large-v3"].files, meeting_speech.LARGE_V3_FILES)
        doc = {"talk": {"device": "cpu", "model": "distil-large-v3"}, "meetings": {"device": "cpu"}}
        self.assertEqual(config._parse_talk(doc, {}).device, "cpu")
        self.assertEqual(config._parse_talk(doc, {}).model, "distil-large-v3")
        self.assertEqual(config._parse_meetings(doc, {}).device, "cpu")
        with self.assertLogs("briefing_reader.config", level="WARNING"):
            self.assertEqual(config._parse_talk({"talk": {"device": "tpu"}}, {}).device, "auto")
        self.assertEqual(config.TalkConfig().device, "auto")
        self.assertEqual(config.MeetingsConfig().device, "auto")


if __name__ == "__main__":
    unittest.main()
