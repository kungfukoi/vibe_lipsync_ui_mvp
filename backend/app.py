import os
import json
import base64
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# --- .env loading (so START_APP doesn't have to export vars) ---
# We try python-dotenv if installed, otherwise we do a tiny local parser.
try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv = None  # type: ignore


def _load_env_file(path: Path) -> None:
    """Best-effort .env loader.

    - Does NOT override existing environment variables.
    - Supports simple KEY=VALUE lines (quotes optional).
    """
    if not path.exists():
        return

    if load_dotenv is not None:
        # Don't override anything already set
        load_dotenv(dotenv_path=str(path), override=False)
        return

    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if not k:
                continue
            if os.getenv(k) is None:
                os.environ[k] = v
    except Exception:
        # Best-effort only
        return

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

# --- Paths ---
HERE = Path(__file__).resolve().parent
# lipsync_ui_mvp root
ROOT_DIR = HERE.parent  # /.../lipsync_ui_mvp
# Load backend/.env into the process environment (best-effort)
_load_env_file(HERE / ".env")


def _resolve_projects_dir() -> Path:
    """Resolve where runtime-generated project files should be stored.

    Vercel serverless functions have a read-only filesystem except for /tmp.
    """
    configured = (os.getenv("PROJECTS_DIR") or "").strip()
    if configured:
        path = Path(configured).expanduser()
    elif os.getenv("VERCEL") == "1":
        path = Path("/tmp") / "lipsync_projects"
    else:
        path = HERE / "projects"

    path.mkdir(parents=True, exist_ok=True)
    return path


PROJECTS_DIR = _resolve_projects_dir()

# --- Fabric pipeline script (kept inside this repo) ---
# We resolve the generator script from within the lipsync_ui_mvp folder so the project is portable.
# You can also override via env var: FABRIC_GENERATOR_PATH
FABRIC_GENERATOR_PATH = os.getenv("FABRIC_GENERATOR_PATH")

_CANDIDATE_GENERATORS = [
    # Preferred: a dedicated tools folder inside the repo
    ROOT_DIR / "tools" / "fabric" / "generate_lines_fabric.py",
    # Alternate: keep the old did_test folder but move it inside the repo
    ROOT_DIR / "did_test" / "generate_lines_fabric.py",
    ROOT_DIR / "tools" / "did_test" / "generate_lines_fabric.py",
]

if FABRIC_GENERATOR_PATH:
    GEN_FABRIC_PY = Path(FABRIC_GENERATOR_PATH)
else:
    GEN_FABRIC_PY = next((p for p in _CANDIDATE_GENERATORS if p.exists()), _CANDIDATE_GENERATORS[0])


import re


def _parse_script_to_lines(script: str) -> List[Dict[str, Any]]:
    """Parse dialogue into a list of {speaker, text, visual}.

    Supported formats (one per line):
      A: hello
      B: hi

      V1: hello
      V2_B: hi
      CUA: hello
      CUb: hi

    Rules:
      - If the prefix is exactly A or B, it's treated as the speaker.
      - Otherwise the prefix is treated as a visual tag.
      - If a visual tag ends with A/B (optionally separated by '_' or '-'), we infer speaker from that suffix.
      - If no speaker can be inferred, speaker is left blank so it can be resolved later from visuals.json (or fall back to A).
    """

    out: List[Dict[str, Any]] = []
    raw_lines = [ln.strip() for ln in (script or "").splitlines() if ln.strip()]

    # Pattern to infer speaker from the end of a visual tag, e.g. V2_B, CUA, CU-B
    infer_pat = re.compile(r"^(?P<tag>.*?)(?:[_-]?(?P<spk>[AB]))$", re.IGNORECASE)

    for ln in raw_lines:
        speaker = ""  # leave blank unless explicitly provided or inferred
        visual = ""
        text = ln

        if ":" in ln:
            left, right = ln.split(":", 1)
            left = left.strip()
            right = right.strip()

            if right:
                left_u = left.upper()

                # Original behavior: A: / B:
                if left_u in {"A", "B"}:
                    speaker = left_u
                    text = right
                else:
                    # New behavior: VISUAL_TAG: text
                    m = infer_pat.match(left)
                    if m:
                        # Normalize visual tag to uppercase for mapping
                        tag = (m.group("tag") or "").strip().upper()
                        spk = (m.group("spk") or "").strip().upper()

                        # If the "tag" part is empty (e.g. "A:"), fall back to original speaker handling
                        if not tag and spk in {"A", "B"}:
                            speaker = spk
                            visual = ""
                        else:
                            # Keep full visual label (including speaker suffix) as the canonical key
                            visual = left_u
                            if spk in {"A", "B"}:
                                speaker = spk
                    else:
                        visual = left_u

                    text = right

        # After parsing, normalize any non A/B speakers to blank
        if speaker not in {"A", "B"}:
            speaker = ""
        out.append({"speaker": speaker, "text": text, "visual": visual})

    return out


def _write_lines_json(project_dir: Path, lines: List[Dict[str, Any]]) -> Path:
    payload: List[Dict[str, Any]] = []
    for i, item in enumerate(lines, start=1):
        payload.append(
            {
                "index": i,
                "id": f"line_{i:03d}",
                "speaker": item["speaker"],
                "text": item["text"],
                "visual": item.get("visual", ""),
            }
        )
    p = project_dir / "lines.json"
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


def _which(cmd: str) -> Optional[str]:
    from shutil import which

    return which(cmd)



# Clamp helper for 0..1 floats with default fallback
def _clamp01(x: Any, default: float) -> float:
    try:
        v = float(x)
    except Exception:
        v = float(default)
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


# ElevenLabs v3 TTD stability quantizer
def _quantize_ttd_stability(x: Any, default: float = 0.5) -> float:
    """ElevenLabs v3 TTD stability only accepts {0.0, 0.5, 1.0}.

    0.0 = Creative, 0.5 = Natural, 1.0 = Robust
    """
    v = _clamp01(x, default)
    # Snap to nearest allowed value
    choices = [0.0, 0.5, 1.0]
    return min(choices, key=lambda c: abs(c - v))


def _eleven_tts_to_wav(
    text: str,
    voice_id: str,
    wav_path: Path,
    voice_settings: Optional[Dict[str, Any]] = None,
) -> None:
    """Generate one WAV using ElevenLabs.

    We request MP3 from ElevenLabs then convert to WAV via ffmpeg.
    """
    api_key = os.getenv("ELEVEN_API_KEY") or os.getenv("XI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ELEVEN_API_KEY not loaded in backend/.env")

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "accept": "audio/mpeg",
        "content-type": "application/json",
    }
    # Allow model override via env var, default to v3
    model_id = os.getenv("ELEVEN_MODEL_ID", "eleven_v3")

    # v3 TTD stability must be one of {0.0, 0.5, 1.0}
    def_stability = _quantize_ttd_stability(os.getenv("ELEVEN_STABILITY", "0.5"), 0.5)
    def_similarity = _clamp01(os.getenv("ELEVEN_SIMILARITY_BOOST", "0.75"), 0.75)
    def_style = _clamp01(os.getenv("ELEVEN_STYLE", "0.35"), 0.35)
    def_speed = os.getenv("ELEVEN_SPEED", "1.0")
    try:
        def_speed_f = float(def_speed)
    except Exception:
        def_speed_f = 1.0

    vs_in = voice_settings or {}

    stability = _quantize_ttd_stability(vs_in.get("stability", def_stability), def_stability)
    similarity_boost = _clamp01(vs_in.get("similarity_boost", def_similarity), def_similarity)
    style = _clamp01(vs_in.get("style", def_style), def_style)

    # Speed is not strictly 0-1; keep it conservative.
    speed_raw = vs_in.get("speed", def_speed_f)
    try:
        speed = float(speed_raw)
    except Exception:
        speed = def_speed_f
    if speed < 0.7:
        speed = 0.7
    if speed > 1.2:
        speed = 1.2

    body = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": stability,
            "similarity_boost": similarity_boost,
            "style": style,
        },
    }

    r = requests.post(url, headers=headers, json=body, timeout=60)
    if r.status_code != 200:
        # Include upstream error details to make debugging fast
        msg = ""
        try:
            # ElevenLabs often returns JSON errors
            msg = json.dumps(r.json())
        except Exception:
            msg = (r.text or "").strip()
        if len(msg) > 800:
            msg = msg[:800] + "..."
        raise HTTPException(status_code=500, detail=f"ElevenLabs TTS failed ({r.status_code}): {msg}")

    mp3_path = wav_path.with_suffix(".mp3")
    mp3_path.write_bytes(r.content)

    ffmpeg = _which("ffmpeg")
    if not ffmpeg:
        raise HTTPException(status_code=500, detail="ffmpeg not found. Install ffmpeg to convert audio.")

    proc = subprocess.run(
        [ffmpeg, "-y", "-i", str(mp3_path), str(wav_path)],
        cwd=str(wav_path.parent),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise HTTPException(status_code=500, detail="ffmpeg audio conversion failed")

    try:
        mp3_path.unlink(missing_ok=True)
    except Exception:
        pass


# --- ElevenLabs Dialogue Mode (Text-to-Dialogue) helper ---
def _eleven_dialogue_to_wav_and_ranges(
    inputs: List[Dict[str, str]],
    wav_path: Path,
    settings: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, float]]:
    """Generate a full dialogue audio in one pass (ElevenLabs Text-to-Dialogue) and return per-input time ranges.

    Returns a list of dicts matching `inputs` order:
      [{"start": float_seconds, "end": float_seconds}, ...]

    We call /v1/text-to-dialogue/with-timestamps and use the returned `voice_segments`
    which includes start/end times and the `dialogue_input_index`.
    """

    api_key = os.getenv("ELEVEN_API_KEY") or os.getenv("XI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ELEVEN_API_KEY not loaded in backend/.env")

    if not inputs:
        raise HTTPException(status_code=400, detail="No dialogue inputs provided")

    # Enforce ElevenLabs limit: max 10 unique voice IDs per request
    uniq = {str(it.get("voice_id", "")).strip() for it in inputs if str(it.get("voice_id", "")).strip()}
    if len(uniq) > 10:
        raise HTTPException(status_code=400, detail="Dialogue mode supports a maximum of 10 unique voice IDs per request")

    ffmpeg = _which("ffmpeg")
    if not ffmpeg:
        raise HTTPException(status_code=500, detail="ffmpeg not found. Install ffmpeg to convert audio.")

    url = "https://api.elevenlabs.io/v1/text-to-dialogue/with-timestamps"
    headers = {
        "xi-api-key": api_key,
        "accept": "application/json",
        "content-type": "application/json",
    }

    model_id = os.getenv("ELEVEN_MODEL_ID", "eleven_v3")

    # Optional settings controlling dialogue generation.
    # Docs expose a `settings` object; keep it best-effort and only pass if a dict is provided.
    body: Dict[str, Any] = {
        "inputs": [{"text": str(i.get("text", "")), "voice_id": str(i.get("voice_id", ""))} for i in inputs],
        "model_id": model_id,
        # Keep default output format (mp3) to avoid subscription constraints; we'll convert to WAV.
        "output_format": os.getenv("ELEVEN_DIALOGUE_OUTPUT_FORMAT", "mp3_44100_128"),
    }

    if isinstance(settings, dict) and settings:
        body["settings"] = settings

    r = requests.post(url, headers=headers, json=body, timeout=180)
    if r.status_code != 200:
        msg = ""
        try:
            msg = json.dumps(r.json())
        except Exception:
            msg = (r.text or "").strip()
        if len(msg) > 800:
            msg = msg[:800] + "..."
        raise HTTPException(status_code=500, detail=f"ElevenLabs Dialogue failed ({r.status_code}): {msg}")

    try:
        data = r.json()
    except Exception:
        raise HTTPException(status_code=500, detail="ElevenLabs Dialogue returned non-JSON response")

    audio_b64 = data.get("audio_base64")
    if not audio_b64:
        raise HTTPException(status_code=500, detail="ElevenLabs Dialogue returned no audio")

    # Decode MP3 and convert to WAV
    mp3_path = wav_path.with_suffix(".mp3")
    try:
        mp3_path.write_bytes(base64.b64decode(audio_b64))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to decode ElevenLabs audio_base64")

    proc = subprocess.run(
        [ffmpeg, "-y", "-i", str(mp3_path), str(wav_path)],
        cwd=str(wav_path.parent),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise HTTPException(status_code=500, detail="ffmpeg audio conversion failed")

    try:
        mp3_path.unlink(missing_ok=True)
    except Exception:
        pass

    voice_segments = data.get("voice_segments") or []
    if not isinstance(voice_segments, list) or not voice_segments:
        # If segments are missing, fall back to naive whole-range per input.
        # Caller can decide to fall back to per-line TTS.
        return [{"start": 0.0, "end": 0.0} for _ in inputs]

    # Build per-input start/end by aggregating segments with the same dialogue_input_index
    ranges: List[Dict[str, float]] = [{"start": 1e9, "end": -1e9} for _ in inputs]

    for seg in voice_segments:
        try:
            di = int(seg.get("dialogue_input_index", -1))
        except Exception:
            di = -1
        if di < 0 or di >= len(inputs):
            continue
        try:
            st = float(seg.get("start_time_seconds", 0.0))
            et = float(seg.get("end_time_seconds", 0.0))
        except Exception:
            continue
        if st < ranges[di]["start"]:
            ranges[di]["start"] = st
        if et > ranges[di]["end"]:
            ranges[di]["end"] = et

    # Normalize ranges; any missing will be marked as 0..0
    out: List[Dict[str, float]] = []
    for r0 in ranges:
        st = float(r0.get("start", 0.0))
        et = float(r0.get("end", 0.0))
        if st > 1e8 or et < 0.0 or et <= st:
            out.append({"start": 0.0, "end": 0.0})
        else:
            out.append({"start": st, "end": et})

    return out


def _convert_audio_to_wav(
    in_audio_path: Path,
    wav_path: Path,
    target_sr: int = 48000,
) -> None:
    """Convert any audio format to a normalized WAV for the pipeline.

    - Ensures WAV output exists and is non-empty.
    - Forces 48kHz sample rate + mono for predictable downstream behavior.
    """

    ffmpeg = _which("ffmpeg")
    if not ffmpeg:
        raise HTTPException(status_code=500, detail="ffmpeg not found. Install ffmpeg to convert audio.")

    if not in_audio_path.exists() or in_audio_path.stat().st_size == 0:
        raise HTTPException(status_code=400, detail=f"Input audio is missing or empty: {in_audio_path.name}")

    # Convert to PCM wav, 48kHz, mono
    proc = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(in_audio_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(int(target_sr)),
            "-c:a",
            "pcm_s16le",
            str(wav_path),
        ],
        cwd=str(wav_path.parent),
        capture_output=True,
        text=True,
    )

    if proc.returncode != 0:
        raise HTTPException(status_code=500, detail="ffmpeg audio conversion failed")

    if (not wav_path.exists()) or wav_path.stat().st_size == 0:
        raise HTTPException(status_code=500, detail="Converted WAV was not created or is empty")

# --- ElevenLabs Speech-to-Speech helper ---
def _eleven_sts_to_wav(
    in_audio_path: Path,
    voice_id: str,
    wav_path: Path,
    voice_settings: Optional[Dict[str, Any]] = None,
) -> None:
    """Convert an uploaded audio performance into the target ElevenLabs voice (Speech-to-Speech).

    Saves a WAV at `wav_path`.
    """
    api_key = os.getenv("ELEVEN_API_KEY") or os.getenv("XI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ELEVEN_API_KEY not loaded in backend/.env")

    ffmpeg = _which("ffmpeg")
    if not ffmpeg:
        raise HTTPException(status_code=500, detail="ffmpeg not found. Install ffmpeg to convert audio.")

    if not in_audio_path.exists() or in_audio_path.stat().st_size == 0:
        raise HTTPException(status_code=400, detail=f"Input audio is missing or empty: {in_audio_path.name}")

    # Endpoint per ElevenLabs docs: /v1/speech-to-speech/{voice_id}/stream
    url = f"https://api.elevenlabs.io/v1/speech-to-speech/{voice_id}/stream"
    headers = {
        "Accept": "application/json",
        "xi-api-key": api_key,
    }

    # Default STS model (multilingual)
    model_id = os.getenv("ELEVEN_STS_MODEL_ID", "eleven_multilingual_sts_v2")

    # STS voice settings are 0..1 (not the v3 TTD stability tri-state)
    def_stability = _clamp01(os.getenv("ELEVEN_STS_STABILITY", "0.5"), 0.5)
    def_similarity = _clamp01(os.getenv("ELEVEN_STS_SIMILARITY_BOOST", "0.75"), 0.75)
    def_style = _clamp01(os.getenv("ELEVEN_STS_STYLE", os.getenv("ELEVEN_STYLE", "0.35")), 0.35)

    vs_in = voice_settings or {}
    stability = _clamp01(vs_in.get("sts_stability", vs_in.get("stability", def_stability)), def_stability)
    similarity_boost = _clamp01(vs_in.get("sts_similarity_boost", vs_in.get("similarity_boost", def_similarity)), def_similarity)
    style = _clamp01(vs_in.get("style", def_style), def_style)

    data = {
        "model_id": model_id,
        # ElevenLabs expects this as a JSON string in form-data
        "voice_settings": json.dumps(
            {
                "stability": stability,
                "similarity_boost": similarity_boost,
                "style": style,
                "use_speaker_boost": True,
            }
        ),
    }

    with open(in_audio_path, "rb") as f:
        files = {"audio": f}
        r = requests.post(url, headers=headers, data=data, files=files, timeout=120)

    if r.status_code != 200:
        msg = ""
        try:
            msg = json.dumps(r.json())
        except Exception:
            msg = (r.text or "").strip()
        if len(msg) > 800:
            msg = msg[:800] + "..."
        raise HTTPException(status_code=500, detail=f"ElevenLabs STS failed ({r.status_code}): {msg}")

    mp3_path = wav_path.with_suffix(".mp3")
    mp3_path.write_bytes(r.content)

    proc = subprocess.run(
        [ffmpeg, "-y", "-i", str(mp3_path), str(wav_path)],
        cwd=str(wav_path.parent),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise HTTPException(status_code=500, detail="ffmpeg audio conversion failed")

    try:
        mp3_path.unlink(missing_ok=True)
    except Exception:
        pass


def _ensure_project_env(project_dir: Path) -> None:
    """Write a minimal .env into the project folder so legacy scripts can find FAL_KEY."""
    fal_key = os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY") or ""
    if not fal_key:
        raise HTTPException(status_code=500, detail="FAL_KEY not loaded in backend/.env")
    (project_dir / ".env").write_text(f"FAL_KEY={fal_key}\n", encoding="utf-8")


# --- Stitched Preview Helper ---
def _stitch_preview(project_dir: Path) -> Optional[Path]:
    """Create a stitched preview mp4 (output_fabric.mp4) if possible.

    We prefer:
      - Intro WS composite: 00_intro_ws_final.mp4 (includes line 1)
      - Then per-line clips: line_002_*.mp4, line_003_*.mp4, ...

    Returns the path to output_fabric.mp4 if created or already exists.
    """

    out_path = project_dir / "output_fabric.mp4"
    if out_path.exists():
        return out_path

    ffmpeg = _which("ffmpeg")
    if not ffmpeg:
        return None

    def _probe_duration_seconds(path: Path) -> float:
        try:
            ffprobe = _which("ffprobe") or "ffprobe"
            proc = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                return 0.0
            return float((proc.stdout or "").strip() or 0.0)
        except Exception:
            return 0.0

    # Prefer masked intro if present; otherwise fall back to Fabric's raw intro
    intro = project_dir / "00_intro_ws_final.mp4"
    if not intro.exists():
        alt = project_dir / "00_ws_full.mp4"
        if alt.exists():
            intro = alt
        else:
            # Final fallback: use first line clip if present
            matches = sorted(project_dir.glob("line_001_*.mp4"))
            if matches:
                intro = matches[0]
            else:
                return None

    # Build ordered clip list based on lines.json
    clips: List[Path] = [intro]
    lines_path = project_dir / "lines.json"
    if lines_path.exists():
        try:
            lines = json.loads(lines_path.read_text(encoding="utf-8"))
            # line 1 is in intro; start at 2
            for item in lines:
                idx = int(item.get("index", 0))
                if idx <= 1:
                    continue
                speaker = str(item.get("speaker", "")).upper() or "A"
                candidate = project_dir / f"line_{idx:03d}_{speaker}.mp4"
                if candidate.exists():
                    clips.append(candidate)
                else:
                    # Fallback: if speaker suffix doesn't match (or differs), grab any line_{idx}_*.mp4
                    matches = sorted(project_dir.glob(f"line_{idx:03d}_*.mp4"))
                    if matches:
                        clips.append(matches[0])
        except Exception:
            pass

    # If we have only intro, still treat it as the preview (no stitching)
    if len(clips) == 1:
        try:
            shutil.copy2(intro, out_path)
            return out_path
        except Exception:
            return intro

    # --- LTX-specific stabilization workaround ---
    tmp_dir = project_dir / "_stitch_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # LTX often "settles" for the first few frames (micro zoom/reframe). Trim a tiny head segment
    # when we know the clips were generated by LTX.
    ltx_mode = False
    try:
        marker = (project_dir / "_renderer.txt").read_text(encoding="utf-8").strip().lower()
        ltx_mode = marker.startswith("ltx")
    except Exception:
        ltx_mode = False

    trim_head_sec = 0.12 if ltx_mode else 0.0

    normalized: List[Path] = []
    for i, src in enumerate(clips):
        dst = tmp_dir / f"clip_{i:03d}.mp4"
        # Re-encode each segment with clean timing and optional head trim
        vf = "setpts=PTS-STARTPTS,fps=30,format=yuv420p"
        af = "asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0"

        if trim_head_sec > 0.0:
            # Trim the first ~3 frames worth of time and rebase timestamps.
            vf = f"trim=start={trim_head_sec},setpts=PTS-STARTPTS,fps=30,format=yuv420p"
            af = f"atrim=start={trim_head_sec},asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0"

        # Clamp segment duration to the corresponding line_XXX.wav length to prevent late visual cuts.
        dur_limit = 0.0
        wav_path: Optional[Path] = None

        m = re.search(r"line_(\d{3})_", src.name)
        if m:
            wav_path = project_dir / f"line_{m.group(1)}.wav"
        elif i == 0:
            # Intro clip contains line 001 audio.
            wav_path = project_dir / "line_001.wav"

        if wav_path is not None and wav_path.exists():
            dur_limit = _probe_duration_seconds(wav_path)

        cmd_norm = [
            ffmpeg,
            "-y",
            "-i",
            str(src),
        ]

        # Hard clamp segment duration to audio length to prevent late visual cuts.
        if dur_limit and dur_limit > 0.05:
            cmd_norm += ["-t", f"{dur_limit:.4f}"]

        cmd_norm += [
            "-shortest",
            # Force constant frame rate + reset timing
            "-vf",
            vf,
            "-af",
            af,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-movflags",
            "+faststart",
            str(dst),
        ]
        p = subprocess.run(cmd_norm, cwd=str(project_dir), capture_output=True, text=True)
        if p.returncode != 0:
            # Save debug info for this project
            (project_dir / "_stitch_error.txt").write_text((p.stderr or "")[-8000:] + "\n" + (p.stdout or "")[-8000:], encoding="utf-8")
            return None
        normalized.append(dst)

    # Concatenate normalized clips (video-only, re-encode)
    concat_txt = tmp_dir / "concat_list.txt"
    concat_txt.write_text("\n".join([f"file '{p.as_posix()}'" for p in normalized]) + "\n", encoding="utf-8")

    tmp_video = tmp_dir / "_video_concat.mp4"

    cmd_cat = [
        ffmpeg,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_txt),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-an",
        str(tmp_video),
    ]

    proc = subprocess.run(cmd_cat, cwd=str(project_dir), capture_output=True, text=True)
    if proc.returncode != 0:
        (project_dir / "_stitch_error.txt").write_text((proc.stderr or "")[-8000:] + "\n" + (proc.stdout or "")[-8000:], encoding="utf-8")
        return None

    if (not tmp_video.exists()) or tmp_video.stat().st_size == 0:
        (project_dir / "_stitch_error.txt").write_text("video concat did not produce output", encoding="utf-8")
        return None

    # Build a concatenated audio track from line_XXX.wav files in order.
    # Add a tiny crossfade to avoid pops at hard cuts.
    wavs: List[Path] = []

    # Prefer lines.json ordering if available
    if lines_path.exists():
        try:
            lines2 = json.loads(lines_path.read_text(encoding="utf-8"))
            for item in lines2:
                idx = int(item.get("index", 0) or 0)
                if idx <= 0:
                    continue
                wp = project_dir / f"line_{idx:03d}.wav"
                if wp.exists():
                    wavs.append(wp)
        except Exception:
            wavs = []

    if not wavs:
        wavs = sorted(project_dir.glob("line_*.wav"))

    xfade_sec = 0.0
    try:
        xfade_sec = float(os.getenv("AUDIO_CROSSFADE_SEC", "0.015") or 0.015)
    except Exception:
        xfade_sec = 0.015
    if xfade_sec < 0.0:
        xfade_sec = 0.0
    if xfade_sec > 0.08:
        xfade_sec = 0.08

    tmp_wav = tmp_dir / "_audio_concat.wav"

    # Preferred: filter-based concat with acrossfade (more pop-resistant)
    if len(wavs) >= 2 and xfade_sec > 0.0:
        cmd_inputs: List[str] = [ffmpeg, "-y"]
        for p in wavs:
            cmd_inputs += ["-i", str(p)]

        # Chain acrossfade: [0:a][1:a]acrossfade=... [a01]; [a01][2:a]acrossfade... etc.
        parts: List[str] = []
        last = "0:a"
        for i in range(1, len(wavs)):
            out = f"a{i:02d}"
            parts.append(
                f"[{last}][{i}:a]acrossfade=d={xfade_sec}:c1=tri:c2=tri[{out}]"
            )
            last = out

        filter_complex = ";".join(parts)

        cmd_aw = (
            cmd_inputs
            + [
                "-filter_complex",
                filter_complex,
                "-map",
                f"[{last}]",
                "-ac",
                "1",
                "-ar",
                "48000",
                "-c:a",
                "pcm_s16le",
                str(tmp_wav),
            ]
        )

        proc_a = subprocess.run(cmd_aw, cwd=str(project_dir), capture_output=True, text=True)
        if proc_a.returncode != 0 or (not tmp_wav.exists()) or tmp_wav.stat().st_size == 0:
            # Fall back to simple concat (no crossfade)
            try:
                tmp_wav.unlink(missing_ok=True)
            except Exception:
                pass
            proc_a = None
    else:
        proc_a = None

    if proc_a is None:
        # Fallback: concat demuxer (no crossfade)
        audio_list = tmp_dir / "audio_list.txt"
        audio_list.write_text("\n".join([f"file '{p.as_posix()}'" for p in wavs]) + "\n", encoding="utf-8")

        cmd_aw = [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(audio_list),
            "-ac",
            "1",
            "-ar",
            "48000",
            "-c:a",
            "pcm_s16le",
            str(tmp_wav),
        ]

        proc_a = subprocess.run(cmd_aw, cwd=str(project_dir), capture_output=True, text=True)

    if proc_a.returncode != 0 or (not tmp_wav.exists()) or tmp_wav.stat().st_size == 0:
        (project_dir / "_stitch_error.txt").write_text(
            (proc_a.stderr or "")[-8000:] + "\n" + (proc_a.stdout or "")[-8000:],
            encoding="utf-8",
        )
        return None

    # Mux video + concatenated audio into the final preview.
    cmd_mux = [
        ffmpeg,
        "-y",
        "-i",
        str(tmp_video),
        "-i",
        str(tmp_wav),
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(out_path),
    ]

    proc_m = subprocess.run(cmd_mux, cwd=str(project_dir), capture_output=True, text=True)
    if proc_m.returncode != 0 or (not out_path.exists()) or out_path.stat().st_size == 0:
        (project_dir / "_stitch_error.txt").write_text((proc_m.stderr or "")[-8000:] + "\n" + (proc_m.stdout or "")[-8000:], encoding="utf-8")
        return None

    return out_path


# --- LTX Audio-to-Video (per-line) helper ---
def _render_ltx_project(pdir: Path, prompt: str = "") -> None:
    """Render per-line mp4 clips using LTX Audio-to-Video on fal.

    For each entry in lines.json:
      - resolves speaker (A/B, default A)
      - resolves visual tag -> image filename via visuals.json
      - uploads image + corresponding line_XXX.wav
      - calls fal-ai/ltx-2-19b/audio-to-video/lora
      - downloads video and saves as line_XXX_{A|B}.mp4 in the project root

    The existing stitching/outputs logic can then run unchanged.
    """

    # Import here so the backend can still run without the dependency installed
    try:
        import fal_client  # type: ignore
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="fal-client not installed. Install in backend venv: pip install fal-client",
        )

    lines_path = pdir / "lines.json"
    visuals_path = pdir / "visuals.json"

    if not lines_path.exists():
        raise HTTPException(status_code=400, detail="lines.json not found")
    if not visuals_path.exists():
        raise HTTPException(status_code=400, detail="visuals.json not found")

    try:
        lines = json.loads(lines_path.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to read lines.json")

    try:
        vdata = json.loads(visuals_path.read_text(encoding="utf-8"))
        vmap = vdata.get("visuals", {}) or {}
    except Exception:
        vmap = {}

    if not isinstance(lines, list) or not lines:
        raise HTTPException(status_code=400, detail="lines.json is empty")
    if not isinstance(vmap, dict) or not vmap:
        raise HTTPException(status_code=400, detail="visuals.json has no visuals mapping")

    # Always-on hidden prompt prefix to clamp camera + motion.
    # This is applied no matter what the user enters in the UI.
    static_prefix = (
        "Tripod-locked static camera. No camera movement. No pan, tilt, zoom, dolly, orbit, or tracking. "
        "Locked framing. Stable composition. "
    )

    # Default prompt tuned for "animate this still" + dialogue performance.
    base_prompt = (
        "Realistic cinematic video. Use the provided image as the first frame and preserve identity, "
        "wardrobe, lighting, and background. Natural facial motion and subtle head movement. "
        "No style shifts, no camera shake, no jump cuts."
    )

    user_prompt = (prompt or "").strip()
    final_prompt = static_prefix + (user_prompt or base_prompt)

    endpoint = "fal-ai/ltx-2-19b/audio-to-video/lora"

    # Pick a stable fallback tag for lines that don't specify a visual
    fallback_tag = next(iter(vmap.keys()))

    # Mark this project as LTX-rendered so stitching can apply LTX-specific cleanup.
    try:
        (pdir / "_renderer.txt").write_text("ltx\n", encoding="utf-8")
    except Exception:
        pass

    for item in lines:
        idx = int(item.get("index", 0) or 0)
        if idx <= 0:
            continue

        speaker = str(item.get("speaker", "") or "").strip().upper()
        if speaker not in {"A", "B"}:
            speaker = "A"

        tag = str(item.get("visual", "") or "").strip().upper() or fallback_tag
        img_name = vmap.get(tag) or vmap.get(fallback_tag)
        if not img_name:
            raise HTTPException(status_code=400, detail=f"No visual image found for tag '{tag}'")

        image_path = pdir / str(img_name)
        audio_path = pdir / f"line_{idx:03d}.wav"
        out_mp4 = pdir / f"line_{idx:03d}_{speaker}.mp4"

        if not image_path.exists():
            raise HTTPException(status_code=400, detail=f"Missing visual file: {img_name}")
        if not audio_path.exists():
            raise HTTPException(status_code=400, detail=f"Missing audio file: {audio_path.name}")

        # Upload media to fal storage
        image_url = fal_client.upload_file(str(image_path))
        audio_url = fal_client.upload_file(str(audio_path))

        # Submit job and wait for result (fine for short per-line clips)
        result = fal_client.subscribe(
            endpoint,
            arguments={
                "prompt": final_prompt,
                "negative_prompt": (
                    "camera shake, handheld, dolly, zoom, pan, tilt, orbit, drifting frame, "
                    "background animation, moving walls, moving objects, warping background, flicker, "
                    "style change, cartoon, CGI, exaggerated motion, jitter, unstable framing"
                ),
                "audio_url": audio_url,
                "image_url": image_url,
                "video_size": "auto",
                "match_audio_length": True,
                "camera_lora": "static",
                "camera_lora_scale": 1.0,
                "fps": 25,
                "video_output_type": "X264 (.mp4)",
                "video_quality": "high",
                "use_multiscale": False,
                "guidance_scale": 4.0,
                "num_inference_steps": 45,
                "enable_prompt_expansion": False,
                "preprocess_audio": True,
                "image_strength": 1.0,
                "audio_strength": 1.0,
                # No LoRAs by default.
                "loras": [],
            },
        )

        video_url = None
        if isinstance(result, dict):
            video_url = (result.get("video") or {}).get("url")

        if not video_url:
            raise HTTPException(status_code=500, detail=f"LTX returned no video url for line {idx}")

        r = requests.get(video_url, timeout=300)
        if r.status_code != 200 or not r.content:
            raise HTTPException(status_code=500, detail=f"Failed to download LTX video for line {idx}")

        out_mp4.write_bytes(r.content)

# --- App ---
app = FastAPI(title="LipSync UI MVP Backend")

# CORS so local Vite and deployed frontends can call the backend.
_default_cors_origins = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:4173",
    "http://localhost:4173",
]
_extra_cors = [o.strip() for o in (os.getenv("CORS_ALLOW_ORIGINS") or "").split(",") if o.strip()]
_cors_origins = list(dict.fromkeys(_default_cors_origins + _extra_cors))
_cors_origin_regex = (os.getenv("CORS_ALLOW_ORIGIN_REGEX") or "").strip() or None
if not _cors_origin_regex and os.getenv("VERCEL") == "1":
    _cors_origin_regex = r"https://.*\.vercel\.app"

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve generated outputs
app.mount("/projects", StaticFiles(directory=str(PROJECTS_DIR)), name="projects")


def _safe_name(name: str) -> str:
    # Very simple filename safety
    keep = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    cleaned = "".join([c if c in keep else "_" for c in name.strip()])
    return cleaned or "scene"


# Allow more characters than _safe_name; keep it predictable
def _safe_tag(tag: str) -> str:
    # Allow more characters than _safe_name; keep it predictable
    keep = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    cleaned = "".join([c if c in keep else "_" for c in (tag or "").strip()])
    return cleaned or "V1"

# --- Visuals Upload Endpoint ---


@app.post("/api/upload_visuals")
async def upload_visuals(
    project_dir: str = Form(...),
    tags_json: str = Form("[]"),
    speakers_json: str = Form("[]"),
    voices_json: str = Form("[]"),
    files: List[UploadFile] = File(...),
) -> JSONResponse:
    """Upload any number of tagged visuals.

    The frontend sends:
      - tags_json: JSON array of tags matching `files` order, e.g. ["V1","V2_B","WS","WS_MASK"]
      - files: the image files
      - speakers_json: optional JSON array of speakers ("A"/"B") per tag, same length as tags_json

    We store:
      - inputs/visual_<TAG>.png
      - visual_<TAG>.png (project root)
      - visuals.json mapping tag -> filename, and speakers map

    NOTE: This does not change the Fabric generator by itself; it just enables flexible asset management.
    """

    pdir = Path(project_dir)
    if not pdir.exists():
        return JSONResponse({"error": "project_dir does not exist"}, status_code=400)

    try:
        tags = json.loads(tags_json)
    except Exception:
        tags = []

    try:
        speakers = json.loads(speakers_json)
    except Exception:
        speakers = []

    try:
        voices = json.loads(voices_json)
    except Exception:
        voices = []

    if not isinstance(tags, list) or not tags:
        return JSONResponse({"error": "tags_json must be a non-empty JSON array"}, status_code=400)

    if len(tags) != len(files):
        return JSONResponse({"error": f"Expected {len(tags)} files, got {len(files)}"}, status_code=400)

    if speakers and (not isinstance(speakers, list) or len(speakers) != len(tags)):
        return JSONResponse({"error": "speakers_json must be a JSON array with same length as tags_json"}, status_code=400)

    if voices and (not isinstance(voices, list) or len(voices) != len(tags)):
        return JSONResponse({"error": "voices_json must be a JSON array with same length as tags_json"}, status_code=400)

    inputs_dir = pdir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    mapping: Dict[str, str] = {}
    speaker_map: Dict[str, str] = {}
    voice_map: Dict[str, str] = {}

    for i, (tag_raw, up) in enumerate(zip(tags, files)):
        tag = str(tag_raw or "").strip().upper()
        if not tag:
            tag = "V1"

        data = await up.read()
        if not data:
            return JSONResponse({"error": f"Uploaded visual for tag '{tag}' is empty"}, status_code=400)

        safe = _safe_tag(tag)
        fname = f"visual_{safe}.png"

        (inputs_dir / fname).write_bytes(data)
        (pdir / fname).write_bytes(data)

        mapping[tag] = fname
        if speakers and i < len(speakers):
            s = str(speakers[i] or "").strip().upper()
            if s in {"A", "B"}:
                speaker_map[tag] = s
        if voices and i < len(voices):
            vid = str(voices[i] or "").strip()
            if vid:
                voice_map[tag] = vid

    (pdir / "visuals.json").write_text(
        json.dumps({"visuals": mapping, "speakers": speaker_map, "voices": voice_map}, indent=2),
        encoding="utf-8",
    )

    return JSONResponse({"ok": True, "count": len(mapping), "visuals": mapping, "speakers": speaker_map, "voices": voice_map})


# --- Masks Upload Endpoint ---

@app.post("/api/upload_masks")
async def upload_masks(
    project_dir: str = Form(""),
    tags_json: str = Form("[]"),
    files: List[UploadFile] = File(...),
) -> JSONResponse:
    """Upload optional per-shot masks (saved by tag).

    The frontend sends:
      - project_dir: existing project folder
      - tags_json: JSON array of tags matching `files` order, e.g. ["1","2","CUA","CUB"]
      - files: mask image files

    We store:
      - inputs/masks/mask_<TAG>.png
      - inputs/masks/masks.json index (for debugging)

    NOTE: The render pipeline will apply these masks per-line based on lines.json `visual` tag
    once the Fabric script is updated to composite them.
    """

    if not project_dir:
        return JSONResponse({"error": "project_dir is required"}, status_code=400)

    pdir = Path(project_dir)
    if not pdir.exists():
        return JSONResponse({"error": "project_dir does not exist"}, status_code=400)

    try:
        tags = json.loads(tags_json)
    except Exception:
        tags = []

    if not isinstance(tags, list) or not tags:
        return JSONResponse({"error": "tags_json must be a non-empty JSON array"}, status_code=400)

    if len(tags) != len(files):
        return JSONResponse({"error": f"Expected {len(tags)} files, got {len(files)}"}, status_code=400)

    masks_dir = pdir / "inputs" / "masks"
    masks_dir.mkdir(parents=True, exist_ok=True)

    saved: List[Dict[str, str]] = []

    for tag_raw, up in zip(tags, files):
        tag = str(tag_raw or "").strip().upper()
        if not tag:
            continue

        data = await up.read()
        if not data:
            return JSONResponse({"error": f"Uploaded mask for tag '{tag}' is empty"}, status_code=400)

        safe = _safe_tag(tag)
        fname = f"mask_{safe}.png"
        (masks_dir / fname).write_bytes(data)
        saved.append({"tag": tag, "file": fname})

    # Write index for debugging
    try:
        (masks_dir / "masks.json").write_text(json.dumps(saved, indent=2), encoding="utf-8")
    except Exception:
        pass

    return JSONResponse({"ok": True, "count": len(saved), "saved": saved})


@app.get("/api/health")
def health() -> Dict[str, Any]:
    # Keep this response compatible with your earlier curl output
    eleven_key = os.getenv("ELEVEN_API_KEY") or os.getenv("XI_API_KEY") or ""
    return {
        "ok": True,
        "ELEVEN_API_KEY_loaded": bool(eleven_key),
        "FAL_KEY_loaded": bool(os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY")),
    }


@app.get("/api/voices")
def voices() -> Dict[str, Any]:
    """Return voice options.

    Primary: fetch from ElevenLabs if ELEVEN_API_KEY is set.
    Fallback: return cached voices file if present.
    """

    cache_path = HERE / "voices_cache.json"

    api_key = os.getenv("ELEVEN_API_KEY") or os.getenv("XI_API_KEY")
    if api_key:
        try:
            r = requests.get(
                "https://api.elevenlabs.io/v1/voices",
                headers={"xi-api-key": api_key},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            voices_list = data.get("voices", [])
            # Normalize fields the UI expects: voice_id + name
            normalized = [
                {"voice_id": v.get("voice_id"), "name": v.get("name")}
                for v in voices_list
                if v.get("voice_id") and v.get("name")
            ]
            try:
                cache_path.write_text(json.dumps({"voices": normalized}, indent=2))
            except Exception:
                pass
            return {"voices": normalized}
        except Exception as e:
            # Fall through to cache
            pass

    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text())
        except Exception:
            pass

    return {
        "voices": [
            {"voice_id": "demo_voice_a", "name": "Demo Voice A"},
            {"voice_id": "demo_voice_b", "name": "Demo Voice B"},
        ]
    }


@app.post("/api/tts")
def tts(payload: Dict[str, Any]) -> JSONResponse:
    """Step 1: create project folder, save script, build lines.json, generate line_XXX.wav files."""

    script = str(payload.get("script", ""))
    if not script.strip():
        return JSONResponse({"error": "script is required"}, status_code=400)

    voice_a = str(payload.get("voice_a", ""))
    voice_b = str(payload.get("voice_b", ""))

    # Optional voice performance controls (v3)
    # - performance: a single 0..1 slider mapped to ElevenLabs `style`
    # - voice_settings: explicit dict {stability, similarity_boost, style, speed}
    performance = payload.get("performance", None)
    voice_settings = payload.get("voice_settings", None)

    resolved_voice_settings: Dict[str, Any] = {}
    if isinstance(voice_settings, dict):
        resolved_voice_settings.update(voice_settings)

    if performance is not None:
        # Map one slider to style exaggeration
        resolved_voice_settings["style"] = performance

    # TTS requires voices (A/B) because we generate speech from text.
    if not voice_a or not voice_b:
        return JSONResponse({"error": "voice_a and voice_b are required"}, status_code=400)

    project_name = _safe_name(str(payload.get("project_name", "scene")))
    run_id = str(int(time.time() * 1000))
    project_dir = PROJECTS_DIR / f"{project_name}_{run_id}"
    (project_dir / "inputs").mkdir(parents=True, exist_ok=True)
    (project_dir / "outputs").mkdir(parents=True, exist_ok=True)

    # Save for traceability
    meta = {
        "script": script,
        "voice_a": voice_a,
        "voice_b": voice_b,
        "project_name": project_name,
        "voice_settings": resolved_voice_settings,
    }
    (project_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (project_dir / "script.txt").write_text(script, encoding="utf-8")

    # Parse and write lines.json
    lines = _parse_script_to_lines(script)
    if not lines:
        return JSONResponse({"error": "No lines found in script"}, status_code=400)

    _write_lines_json(project_dir, lines)

    # Generate WAVs
    for i, item in enumerate(lines, start=1):
        speaker = item["speaker"]
        text = item["text"]
        voice_id = voice_a if speaker == "A" else voice_b
        wav_path = project_dir / f"line_{i:03d}.wav"
        _eleven_tts_to_wav(text, voice_id, wav_path, resolved_voice_settings)

    # Ensure .env exists for legacy Fabric scripts
    _ensure_project_env(project_dir)

    return JSONResponse({"project_dir": str(project_dir)})


# --- Audio Upload mode: Speech-to-Speech ---
@app.post("/api/sts")
async def sts(
    project_name: str = Form("scene"),
    voice_a: str = Form(""),
    voice_b: str = Form(""),
    performance: float = Form(0.35),
    use_native_audio: bool = Form(False),
    speakers_json: str = Form("[]"),
    visual_tags_json: str = Form("[]"),
    voices_json: str = Form("[]"),
    audios: List[UploadFile] = File(...),
) -> JSONResponse:
    """Audio mode: user uploads per-line audio, we convert it to selected voices via STS.

    Produces line_XXX.wav files in a new project folder, plus lines.json.
    """

    # Only require A/B voices when we are doing STS conversion.
    # If we're using native audio passthrough, voices are ignored.
    if (not use_native_audio) and (not voice_a or not voice_b):
        return JSONResponse({"error": "voice_a and voice_b are required"}, status_code=400)

    try:
        speakers = json.loads(speakers_json)
    except Exception:
        speakers = []

    try:
        visual_tags = json.loads(visual_tags_json)
    except Exception:
        visual_tags = []

    try:
        voices = json.loads(voices_json)
    except Exception:
        voices = []

    if not isinstance(speakers, list) or not speakers:
        return JSONResponse({"error": "speakers_json must be a non-empty JSON array like ['A','B',...]"}, status_code=400)

    if len(audios) != len(speakers):
        return JSONResponse({"error": f"Expected {len(speakers)} audio files, got {len(audios)}"}, status_code=400)

    if visual_tags:
        if not isinstance(visual_tags, list) or len(visual_tags) != len(speakers):
            return JSONResponse({"error": "visual_tags_json must be a JSON array with same length as speakers_json"}, status_code=400)
    else:
        visual_tags = [""] * len(speakers)

    if voices and (not isinstance(voices, list) or len(voices) != len(speakers)):
        return JSONResponse({"error": "voices_json must be a JSON array with same length as speakers_json"}, status_code=400)

    project_name_safe = _safe_name(project_name)
    run_id = str(int(time.time() * 1000))
    project_dir = PROJECTS_DIR / f"{project_name_safe}_{run_id}"
    (project_dir / "inputs").mkdir(parents=True, exist_ok=True)
    (project_dir / "outputs").mkdir(parents=True, exist_ok=True)

    # Reuse existing voice settings plumbing; performance maps to style
    resolved_voice_settings: Dict[str, Any] = {"style": _clamp01(performance, 0.35)}

    meta = {
        "script": "",
        "mode": "audio",
        "use_native_audio": bool(use_native_audio),
        "voice_a": voice_a,
        "voice_b": voice_b,
        "project_name": project_name_safe,
        "voice_settings": resolved_voice_settings,
        "speakers": speakers,
        "visual_tags": visual_tags,
    }
    (project_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # Build lines.json using speakers + optional per-line visual tags; text is empty placeholders
    lines: List[Dict[str, Any]] = []
    for i, (sp, vt) in enumerate(zip(speakers, visual_tags)):
        s = str(sp).strip().upper() if str(sp).strip() else "A"
        if s not in {"A", "B"}:
            s = "A"
        v = str(vt or "").strip().upper()
        vid = ""
        if voices and i < len(voices):
            vid = str(voices[i] or "").strip()
        line_obj: Dict[str, Any] = {"speaker": s, "text": "", "visual": v}
        if vid:
            line_obj["voice_id"] = vid
        lines.append(line_obj)

    _write_lines_json(project_dir, lines)

    # Save uploaded audio.
    # If use_native_audio=True: keep the user's performance and just normalize to WAV.
    # If use_native_audio=False: run ElevenLabs Speech-to-Speech to match selected voices.
    for i, (up, item) in enumerate(zip(audios, lines), start=1):
        speaker = item["speaker"]
        # Voice selection only matters for STS conversion.
        # In native mode, this value is ignored.
        target_voice = str(item.get("voice_id", "") or "").strip() or (
            (voice_a if speaker == "A" else voice_b) if (voice_a and voice_b) else ""
        )

        raw_bytes = await up.read()
        if not raw_bytes:
            return JSONResponse({"error": f"Uploaded audio for line {i} is empty"}, status_code=400)

        in_suffix = Path(up.filename or "").suffix or ".wav"
        in_path = project_dir / f"line_{i:03d}_in{in_suffix}"
        in_path.write_bytes(raw_bytes)

        wav_path = project_dir / f"line_{i:03d}.wav"

        if use_native_audio:
            # Preserve the original performance (no voice conversion), just normalize.
            _convert_audio_to_wav(in_path, wav_path)
        else:
            # Convert performance to selected voices.
            _eleven_sts_to_wav(in_path, target_voice, wav_path, resolved_voice_settings)

        # Keep project folder tidy
        try:
            in_path.unlink(missing_ok=True)
        except Exception:
            pass

    _ensure_project_env(project_dir)
    return JSONResponse({"project_dir": str(project_dir)})


@app.post("/api/upload_inputs")
async def upload_inputs(
    project_dir: str = Form(...),
    ws: UploadFile = File(...),
    ws_mask: UploadFile | None = File(None),
    cu_a: UploadFile = File(...),
    cu_b: UploadFile = File(...),
) -> JSONResponse:
    pdir = Path(project_dir)
    if not pdir.exists():
        return JSONResponse({"error": "project_dir does not exist"}, status_code=400)

    inputs_dir = pdir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    async def save_to_paths(up: UploadFile, out_paths: List[Path]) -> None:
        data = await up.read()
        for out_path in out_paths:
            out_path.write_bytes(data)

    await save_to_paths(ws, [inputs_dir / "ws.png", pdir / "ws.png"])
    if ws_mask is not None:
        await save_to_paths(ws_mask, [inputs_dir / "ws_mask.png", pdir / "ws_mask.png"])
    await save_to_paths(cu_a, [inputs_dir / "cu_a.png", pdir / "cu_a.png"])
    await save_to_paths(cu_b, [inputs_dir / "cu_b.png", pdir / "cu_b.png"])

    return JSONResponse({"ok": True})


@app.post("/api/render")
def render(payload: Dict[str, Any]) -> JSONResponse:
    """Step 3: run the selected renderer (fabric or ltx) in the project folder and return outputs."""

    project_dir = payload.get("project_dir")
    if not project_dir:
        return JSONResponse({"error": "project_dir is required"}, status_code=400)

    pdir = Path(project_dir)
    if not pdir.exists():
        return JSONResponse({"error": "project_dir not found"}, status_code=400)

    # Ensure .env exists for legacy script
    _ensure_project_env(pdir)

    renderer = str(payload.get("renderer", "fabric") or "fabric").strip().lower()
    if renderer not in {"fabric", "ltx"}:
        return JSONResponse({"error": "renderer must be 'fabric' or 'ltx'"}, status_code=400)

    ltx_prompt = str(payload.get("ltx_prompt", "") or "")

    # Required inputs
    missing = []
    for needed in ["lines.json", "visuals.json"]:
        if not (pdir / needed).exists():
            missing.append(needed)
    if missing:
        return JSONResponse({"error": f"Missing: {', '.join(missing)}"}, status_code=400)

    # Sanity: at least one visual image should exist (visuals.json points to files in project root)
    try:
        vdata = json.loads((pdir / "visuals.json").read_text(encoding="utf-8"))
        vmap = vdata.get("visuals", {})
    except Exception:
        vmap = {}

    if not isinstance(vmap, dict) or not vmap:
        return JSONResponse({"error": "visuals.json has no visuals mapping"}, status_code=400)

    any_exists = False
    for fname in vmap.values():
        if fname and (pdir / str(fname)).exists():
            any_exists = True
            break

    if not any_exists:
        return JSONResponse({"error": "No visual image files found for visuals.json mapping"}, status_code=400)

    # Audio check
    if not list(pdir.glob("line_*.wav")):
        return JSONResponse({"error": "No line_XXX.wav audio found. Run Step 1/3 first."}, status_code=400)

    if renderer == "fabric":
        if not GEN_FABRIC_PY.exists():
            return JSONResponse(
                {
                    "error": f"Missing Fabric generator script. Expected at: {GEN_FABRIC_PY}. (Set FABRIC_GENERATOR_PATH to override.)",
                },
                status_code=500,
            )

        proc = subprocess.run(
            ["python3", str(GEN_FABRIC_PY)],
            cwd=str(pdir),
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )

        if proc.returncode != 0:
            return JSONResponse(
                {
                    "error": "Fabric generator failed",
                    "stderr": (proc.stderr or "")[-4000:],
                    "stdout": (proc.stdout or "")[-4000:],
                },
                status_code=500,
            )

    else:
        # LTX path: render per-line mp4s, then reuse the same stitching + outputs handling below
        try:
            _render_ltx_project(pdir, prompt=ltx_prompt)
        except HTTPException as e:
            return JSONResponse({"error": str(e.detail)}, status_code=e.status_code)
        except Exception as e:
            return JSONResponse({"error": f"LTX render failed: {e}"}, status_code=500)

    stitched = _stitch_preview(pdir)
    if stitched is None:
        # Hard fallback: ensure a stable preview file exists
        intro = pdir / "00_intro_ws_final.mp4"
        outp = pdir / "output_fabric.mp4"
        try:
            if intro.exists() and not outp.exists():
                shutil.copy2(intro, outp)
        except Exception:
            pass
    outputs_dir = pdir / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)

    # Collect mp4 outputs produced in project root.
    # Hide the unmasked debug WS (00_ws_full.mp4) from the UI list.
    mp4s = sorted([p.name for p in pdir.glob("*.mp4") if p.name != "00_ws_full.mp4"])
    out_names: List[str] = []
    for name in mp4s:
        src = pdir / name
        dst = outputs_dir / name
        try:
            shutil.copy2(src, dst)
            out_names.append(name)
        except Exception:
            pass

    # Lock preview to the stitched file (or intro-copied fallback)
    preview = "output_fabric.mp4" if (outputs_dir / "output_fabric.mp4").exists() else ""

    if not preview:
        # Fallback: pick the first mp4 we copied to outputs
        mp4s_out = sorted([p.name for p in outputs_dir.glob("*.mp4")])
        if mp4s_out:
            # Also create output_fabric.mp4 for UI consistency
            try:
                shutil.copy2(outputs_dir / mp4s_out[0], outputs_dir / "output_fabric.mp4")
                preview = "output_fabric.mp4"
                if "output_fabric.mp4" not in out_names:
                    out_names.append("output_fabric.mp4")
            except Exception:
                preview = mp4s_out[0]

    if not preview:
        return JSONResponse({"error": "Preview could not be created"}, status_code=500)

    return JSONResponse({"outputs_dir": str(outputs_dir), "outputs": out_names, "preview": preview})


if __name__ == "__main__":
    # Convenience for running directly
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")

# --- Project from Script endpoint (no audio generated yet) ---


@app.post("/api/project_from_script")
def project_from_script(payload: Dict[str, Any]) -> JSONResponse:
    """Create a project folder + lines.json from a script, without generating audio yet.

    This enables workflows where speaker/voice is decided later via visual slot assignments.
    """

    script = str(payload.get("script", ""))
    if not script.strip():
        return JSONResponse({"error": "script is required"}, status_code=400)

    voice_a = str(payload.get("voice_a", ""))
    voice_b = str(payload.get("voice_b", ""))

    performance = payload.get("performance", None)
    voice_settings = payload.get("voice_settings", None)

    resolved_voice_settings: Dict[str, Any] = {}
    if isinstance(voice_settings, dict):
        resolved_voice_settings.update(voice_settings)
    if performance is not None:
        resolved_voice_settings["style"] = performance

    if not voice_a or not voice_b:
        return JSONResponse({"error": "voice_a and voice_b are required"}, status_code=400)

    project_name = _safe_name(str(payload.get("project_name", "scene")))
    run_id = str(int(time.time() * 1000))
    project_dir = PROJECTS_DIR / f"{project_name}_{run_id}"
    (project_dir / "inputs").mkdir(parents=True, exist_ok=True)
    (project_dir / "outputs").mkdir(parents=True, exist_ok=True)

    meta = {
        "script": script,
        "voice_a": voice_a,
        "voice_b": voice_b,
        "project_name": project_name,
        "voice_settings": resolved_voice_settings,
        "mode": "script_no_audio",
    }
    (project_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (project_dir / "script.txt").write_text(script, encoding="utf-8")

    lines = _parse_script_to_lines(script)
    if not lines:
        return JSONResponse({"error": "No lines found in script"}, status_code=400)

    _write_lines_json(project_dir, lines)
    _ensure_project_env(project_dir)

    return JSONResponse({"project_dir": str(project_dir)})


# --- Generate audio from lines.json + visuals.json speakers ---

@app.post("/api/generate_audio")
def generate_audio(payload: Dict[str, Any]) -> JSONResponse:
    """Generate line_XXX.wav for an existing project.

    Speaker/voice resolution order per line:
      1) Explicit per-tag voice_id from visuals.json (multi-voice)
      2) Explicit speaker already in lines.json (A/B)
      3) visuals.json speakers mapping by visual tag
      4) Fallback to A

    Also writes back updated lines.json with the resolved speaker.
    """

    project_dir = payload.get("project_dir")
    if not project_dir:
        return JSONResponse({"error": "project_dir is required"}, status_code=400)

    pdir = Path(project_dir)
    if not pdir.exists():
        return JSONResponse({"error": "project_dir not found"}, status_code=400)

    voice_a = str(payload.get("voice_a", ""))
    voice_b = str(payload.get("voice_b", ""))
    if not voice_a or not voice_b:
        return JSONResponse({"error": "voice_a and voice_b are required"}, status_code=400)

    performance = payload.get("performance", None)
    voice_settings = payload.get("voice_settings", None)

    resolved_voice_settings: Dict[str, Any] = {}
    if isinstance(voice_settings, dict):
        resolved_voice_settings.update(voice_settings)
    if performance is not None:
        resolved_voice_settings["style"] = performance

    lines_path = pdir / "lines.json"
    if not lines_path.exists():
        return JSONResponse({"error": "lines.json not found"}, status_code=400)

    try:
        lines = json.loads(lines_path.read_text(encoding="utf-8"))
    except Exception:
        return JSONResponse({"error": "Failed to read lines.json"}, status_code=400)

    if not isinstance(lines, list) or not lines:
        return JSONResponse({"error": "lines.json is empty"}, status_code=400)

    speaker_map: Dict[str, str] = {}
    voice_map: Dict[str, str] = {}
    vmap_path = pdir / "visuals.json"
    if vmap_path.exists():
        try:
            vdata = json.loads(vmap_path.read_text(encoding="utf-8"))

            sm = vdata.get("speakers", {})
            if isinstance(sm, dict):
                speaker_map = {str(k).strip().upper(): str(v).strip().upper() for k, v in sm.items()}

            vm = vdata.get("voices", {})
            if isinstance(vm, dict):
                # Voice IDs are case-sensitive; keep the value as-is
                voice_map = {str(k).strip().upper(): str(v).strip() for k, v in vm.items() if str(v).strip()}
        except Exception:
            speaker_map = {}
            voice_map = {}

    # Generate WAVs
    dialogue_mode_requested = bool(payload.get("use_dialogue_mode", False))
    use_dialogue_mode = dialogue_mode_requested
    dialogue_fallback_reason = ""

    # Normalize leading emotion tags like "[angry] hello" to a more timestamp-friendly form.
    # We keep the intent by converting to parentheses: "(angry) hello".
    _lead_tag_re = re.compile(r"^\s*\[([^\]]{1,32})\]\s*(.*)$")

    def _normalize_emotion_tag(text: str) -> str:
        t = (text or "").strip()
        m = _lead_tag_re.match(t)
        if not m:
            return t
        tag = (m.group(1) or "").strip()
        rest = (m.group(2) or "").strip()
        if not tag:
            return t
        if not rest:
            # Avoid empty spoken lines; keep a tiny utterance so timestamps exist.
            rest = "..."
        return f"({tag}) {rest}".strip()

    if use_dialogue_mode:
        # First resolve voices and build dialogue inputs in order.
        dialogue_inputs: List[Dict[str, str]] = []
        line_indices: List[int] = []

        for item in lines:
            idx = int(item.get("index", 0) or 0)
            if idx <= 0:
                continue

            tag = str(item.get("visual", "") or "").strip().upper()
            voice_id_by_tag = voice_map.get(tag, "") if tag else ""

            sp = str(item.get("speaker", "") or "").strip().upper()
            if sp not in {"A", "B"}:
                sp = ""

            if not sp and tag:
                mapped = speaker_map.get(tag, "")
                if mapped in {"A", "B"}:
                    sp = mapped

            if not sp:
                sp = "A"

            item["speaker"] = sp

            text = str(item.get("text", "") or "").strip()
            text = _normalize_emotion_tag(text)
            if not text:
                # Keep place, but empty text will produce odd alignment; hard fail.
                raise HTTPException(status_code=400, detail=f"Line {idx} has empty text")

            voice_id = voice_id_by_tag or (voice_a if sp == "A" else voice_b)
            if not voice_id:
                raise HTTPException(status_code=400, detail=f"Line {idx} has no resolved voice_id")

            dialogue_inputs.append({"text": text, "voice_id": voice_id})
            line_indices.append(idx)

        full_wav = pdir / "_dialogue_full.wav"
        ranges = _eleven_dialogue_to_wav_and_ranges(dialogue_inputs, full_wav, settings=None)

        # Determine full WAV duration (seconds) for range repair.
        wav_duration = 0.0
        try:
            ffprobe = _which("ffprobe") or "ffprobe"
            proc_d = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(full_wav),
                ],
                capture_output=True,
                text=True,
            )
            if proc_d.returncode == 0:
                wav_duration = float((proc_d.stdout or "").strip() or 0.0)
        except Exception:
            wav_duration = 0.0

        # If we didn't get usable ranges, fall back to per-line TTS.
        usable = any(r.get("end", 0.0) > r.get("start", 0.0) for r in ranges)
        if not usable:
            dialogue_fallback_reason = "no usable timestamp ranges returned"
            # Cleanup and fall back
            try:
                full_wav.unlink(missing_ok=True)
            except Exception:
                pass
            use_dialogue_mode = False
        else:
            ffmpeg = _which("ffmpeg")
            if not ffmpeg:
                raise HTTPException(status_code=500, detail="ffmpeg not found. Install ffmpeg to split audio.")

            # Split per line using the returned per-input ranges
            # Padding can cause overlap/bleed between adjacent lines (you'll hear the next line early).
            # Default to 0.0 and rely on tiny fades/crossfade later to avoid pops.
            try:
                pad = float(os.getenv("ELEVEN_DIALOGUE_PAD_SEC", "0.0") or 0.0)
            except Exception:
                pad = 0.0
            if pad < 0.0:
                pad = 0.0
            if pad > 0.05:
                pad = 0.05

            for i, idx in enumerate(line_indices):
                r0 = ranges[i] if i < len(ranges) else {"start": 0.0, "end": 0.0}
                st = float(r0.get("start", 0.0) or 0.0)
                et = float(r0.get("end", 0.0) or 0.0)
                if et <= st:
                    # Try to repair a single bad range using neighboring timestamps so we can keep Dialogue Mode.
                    prev_end = 0.0
                    if i > 0:
                        try:
                            prev_end = float((ranges[i - 1] or {}).get("end", 0.0) or 0.0)
                        except Exception:
                            prev_end = 0.0

                    next_start = 0.0
                    if i + 1 < len(ranges):
                        try:
                            next_start = float((ranges[i + 1] or {}).get("start", 0.0) or 0.0)
                        except Exception:
                            next_start = 0.0
                    else:
                        next_start = wav_duration if wav_duration > 0.0 else prev_end

                    # Repair only if we have forward space to carve out a minimal segment.
                    if next_start > prev_end + 0.05:
                        st = max(prev_end, 0.0)
                        et = max(next_start, st + 0.05)
                        # Update local values for splitting
                        st2 = max(0.0, st - pad)
                        et2 = max(st2, et + pad)
                        try:
                            ranges[i]["start"] = st
                            ranges[i]["end"] = et
                        except Exception:
                            pass
                    else:
                        # If we can't repair, fall back to per-line TTS for the whole project.
                        dialogue_fallback_reason = f"missing/invalid range at line {idx}"
                        use_dialogue_mode = False
                        break

                # Prevent overlap into the next line by clamping end to the next start.
                if i + 1 < len(ranges):
                    try:
                        next_st = float((ranges[i + 1] or {}).get("start", 0.0) or 0.0)
                    except Exception:
                        next_st = 0.0
                    if next_st > 0.0 and et > next_st:
                        et = next_st

                # Use duration-based trimming to avoid ambiguity and prevent accidental overlap.
                st2 = max(0.0, st - pad)
                et2 = max(st2, et + pad)

                dur = max(0.02, et2 - st2)

                # Tiny fades to avoid pops (we no longer rely on overlap padding).
                fade_in = 0.005
                fade_out = 0.007
                if dur < (fade_in + fade_out + 0.01):
                    fade_in = 0.0
                    fade_out = 0.0
                fade_out_start = max(0.0, dur - fade_out)

                afilt = ""
                if fade_in > 0.0 and fade_out > 0.0:
                    afilt = f"afade=t=in:st=0:d={fade_in},afade=t=out:st={fade_out_start:.4f}:d={fade_out}"

                out_wav = pdir / f"line_{idx:03d}.wav"

                cmd = [
                    ffmpeg,
                    "-y",
                    "-ss",
                    f"{st2:.4f}",
                    "-t",
                    f"{dur:.4f}",
                    "-i",
                    str(full_wav),
                    "-vn",
                ]

                if afilt:
                    cmd += ["-af", afilt]

                cmd += [
                    "-ac",
                    "1",
                    "-ar",
                    "48000",
                    "-c:a",
                    "pcm_s16le",
                    str(out_wav),
                ]

                proc = subprocess.run(
                    cmd,
                    cwd=str(pdir),
                    capture_output=True,
                    text=True,
                )

                if proc.returncode != 0 or (not out_wav.exists()) or out_wav.stat().st_size == 0:
                    dialogue_fallback_reason = "ffmpeg split failed"
                    use_dialogue_mode = False
                    break

            # Cleanup temp file
            try:
                full_wav.unlink(missing_ok=True)
            except Exception:
                pass

    if dialogue_mode_requested and (not use_dialogue_mode) and (not dialogue_fallback_reason):
        dialogue_fallback_reason = "fallback to per-line TTS"
    if not use_dialogue_mode:
        # Fallback: generate per-line with standard TTS
        for item in lines:
            idx = int(item.get("index", 0) or 0)
            if idx <= 0:
                continue

            tag = str(item.get("visual", "") or "").strip().upper()
            voice_id_by_tag = voice_map.get(tag, "") if tag else ""

            sp = str(item.get("speaker", "") or "").strip().upper()
            if sp not in {"A", "B"}:
                sp = ""

            if not sp and tag:
                mapped = speaker_map.get(tag, "")
                if mapped in {"A", "B"}:
                    sp = mapped

            if not sp:
                sp = "A"

            item["speaker"] = sp

            text = str(item.get("text", "") or "")
            voice_id = voice_id_by_tag or (voice_a if sp == "A" else voice_b)
            wav_path = pdir / f"line_{idx:03d}.wav"
            _eleven_tts_to_wav(text, voice_id, wav_path, resolved_voice_settings)

    # Write updated lines.json
    lines_path.write_text(json.dumps(lines, indent=2), encoding="utf-8")

    _ensure_project_env(pdir)
    return JSONResponse(
        {
            "ok": True,
            "project_dir": str(pdir),
            "dialogue_mode_requested": dialogue_mode_requested,
            "dialogue_mode_used": bool(dialogue_mode_requested and use_dialogue_mode),
            "dialogue_fallback_reason": dialogue_fallback_reason,
        }
    )