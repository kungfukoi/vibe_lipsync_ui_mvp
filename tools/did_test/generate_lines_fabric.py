IMAGE_URL_CACHE: dict[str, str] = {}
import os
import json
import time
import urllib.request
import subprocess
import math
from typing import Optional, Tuple, List

from PIL import Image, ImageFilter

import fal_client

# --------------------------------------------------
# Load .env file if present (expects: FAL_KEY=...)
# --------------------------------------------------
if os.path.exists(".env"):
    with open(".env", "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# --------------------------------------------------
# Config
# --------------------------------------------------
MODEL = "veed/fabric-1.0"
RESOLUTION = "720p"

LINES_JSON = "lines.json"
VISUALS_JSON = "visuals.json"


# MVP rule: intro uses line 1 audio; line 1 speaker must be A
INTRO_SPEAKER = "A"


# --------------------------------------------------
# Helpers
# --------------------------------------------------

def must_exist(path: str):
    if not os.path.exists(path):
        raise SystemExit(f"Missing file: {path}")


def download(url: str, out_path: str):
    urllib.request.urlretrieve(url, out_path)


def run_fabric(image_url: str, audio_url: str):
    # Fabric requires audio_url
    return fal_client.run(
        MODEL,
        arguments={
            "image_url": image_url,
            "audio_url": audio_url,
            "resolution": RESOLUTION,
        },
    )


# Helper: probe video dimensions using ffprobe
def _probe_video_dims(path: str) -> Optional[Tuple[int, int]]:
    """Return (width, height) for a video file using ffprobe."""
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            path,
        ]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            return None
        s = (p.stdout or "").strip()
        if not s or "x" not in s:
            return None
        w_s, h_s = s.split("x", 1)
        w = int(w_s.strip())
        h = int(h_s.strip())
        if w <= 0 or h <= 0:
            return None
        return (w, h)
    except Exception:
        return None


def _mask_bbox(mask_png: str, thresh: int = 10) -> Optional[Tuple[int, int, int, int]]:
    """Return bbox (l, t, r, b) of white-ish pixels in mask."""
    im = Image.open(mask_png).convert("L")
    # Threshold to binary
    im = im.point(lambda p: 255 if p >= thresh else 0)
    bbox = im.getbbox()
    return bbox


def _pad_bbox(bbox: Tuple[int, int, int, int], w: int, h: int, pad_px: int) -> Tuple[int, int, int, int]:
    l, t, r, b = bbox
    l = max(0, l - pad_px)
    t = max(0, t - pad_px)
    r = min(w, r + pad_px)
    b = min(h, b + pad_px)
    # Ensure non-empty
    if r <= l:
        r = min(w, l + 1)
    if b <= t:
        b = min(h, t + 1)
    return (l, t, r, b)


def _target_dims_for_still(w: int, h: int) -> Tuple[int, int, float, float]:
    """Compute output dims that approximate '720p follows aspect'.

    We constrain the shorter side to 720px and cap the longer side at 1280px.
    Returns (out_w, out_h, sx, sy) where sx,sy map original coords to output coords.
    """
    if w <= 0 or h <= 0:
        return (1280, 720, 1.0, 1.0)

    if w >= h:
        # Landscape-ish: aim for 720 tall
        out_h = 720
        out_w = int(round(w * (out_h / float(h))))
        if out_w > 1280:
            out_w = 1280
            out_h = int(round(h * (out_w / float(w))))
    else:
        # Portrait-ish: aim for 720 wide
        out_w = 720
        out_h = int(round(h * (out_w / float(w))))
        if out_h > 1280:
            out_h = 1280
            out_w = int(round((out_h / float(h)) * w))

    out_w = max(1, out_w)
    out_h = max(1, out_h)
    sx = out_w / float(w)
    sy = out_h / float(h)
    return (out_w, out_h, sx, sy)


def composite_patch_on_still(
    base_png: str,
    patch_mp4: str,
    patch_mask_png: str,
    bbox_orig: Tuple[int, int, int, int],
    out_mp4: str,
    target_wh: Optional[Tuple[int, int]] = None,
):
    """Composite a Fabric patch video back onto the full still.

    - base_png: full frame still
    - patch_mp4: Fabric output rendered from a CROPPED still
    - patch_mask_png: CROPPED mask (white=show patch)
    - bbox_orig: crop box in ORIGINAL base_png pixel coords (l,t,r,b)
    - out_mp4: final full frame video

    We scale the base still to an output size (720p-ish), scale the patch to the
    scaled bbox size, then overlay at the scaled bbox position.

    Audio is kept from patch_mp4.
    """

    # In case you ever need it, allow inversion via env
    invert = os.getenv("FABRIC_PATCH_INVERT_MASK", "0").strip() in ("1", "true", "True")

    with Image.open(base_png) as im:
        bw, bh = im.size

    # IMPORTANT: match Fabric's actual output dimensions to avoid bbox/mask distortion.
    if target_wh and target_wh[0] > 0 and target_wh[1] > 0:
        out_w, out_h = target_wh
        sx = out_w / float(bw)
        sy = out_h / float(bh)
    else:
        out_w, out_h, sx, sy = _target_dims_for_still(bw, bh)

    l, t, r, b = bbox_orig
    x = int(round(l * sx))
    y = int(round(t * sy))
    w = max(1, int(round((r - l) * sx)))
    h = max(1, int(round((b - t) * sy)))

    mask_chain = "format=gray"
    if invert:
        mask_chain += ",negate"

    # Inputs:
    # 0: patch video (with audio)
    # 1: base still
    # 2: patch mask
    filter_graph = (
        # Scale base to target size
        f"[1:v]scale={out_w}:{out_h},format=rgba[bg];"
        # Scale patch video to bbox size
        f"[0:v]scale={w}:{h},format=rgba[patch];"
        # Scale mask to bbox size and turn into alpha
        f"[2:v]scale={w}:{h},{mask_chain}[mask_g];"
        # Apply alpha to patch
        "[patch][mask_g]alphamerge[patch_a];"
        # Overlay patch onto background at bbox position
        f"[bg][patch_a]overlay={x}:{y}:format=auto[outv]"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        patch_mp4,
        "-loop",
        "1",
        "-i",
        base_png,
        "-loop",
        "1",
        "-i",
        patch_mask_png,
        "-filter_complex",
        filter_graph,
        "-map",
        "[outv]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "24",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        out_mp4,
    ]

    subprocess.run(cmd, check=True)



def prepare_crop_inputs(
    still_png: str,
    mask_png: str,
    tag: str,
    pad_px: int = 80,
) -> Optional[Tuple[str, str, Tuple[int, int, int, int]]]:
    """Create cropped still+mask files (speaker region) and return (crop_still, crop_mask, bbox_orig)."""

    bbox = _mask_bbox(mask_png)
    if not bbox:
        return None

    with Image.open(still_png) as im:
        w, h = im.size
        bbox_p = _pad_bbox(bbox, w, h, pad_px)
        crop = im.crop(bbox_p)

    with Image.open(mask_png) as mm:
        mm_l = mm.convert("L")
        mm_c = mm_l.crop(bbox_p)

    tmp_dir = "_fabric_crop_tmp"
    os.makedirs(tmp_dir, exist_ok=True)

    crop_still = os.path.join(tmp_dir, f"crop_{tag}.png")
    crop_mask = os.path.join(tmp_dir, f"crop_mask_{tag}.png")

    crop.save(crop_still)
    mm_c.save(crop_mask)

    return (crop_still, crop_mask, bbox_p)



# --- NEW HELPERS: Fabric pre-mask and restore composite ---
def prepare_fabric_input_with_mask(
    still_png: str,
    mask_png: str,
    tag: str,
    fill_rgb: Tuple[int, int, int] = (0, 0, 0),
) -> Tuple[str, str]:
    """Prepare a Fabric input image that hides everything OUTSIDE the speaker mask.

    Mask convention:
      - WHITE = speaker region we WANT Fabric to see/animate
      - BLACK = everything else (hide from Fabric)

    Seam reduction:
      1) Build a CORE mask by eroding inward to avoid boundary contamination.
      2) Replace the hidden area with a BLURRED version of the still (instead of solid black).

    Returns:
      (fabric_input_png, core_mask_png)
    """

    tmp_dir = "_fabric_mask_tmp"
    os.makedirs(tmp_dir, exist_ok=True)

    out_png = os.path.join(tmp_dir, f"fabric_input_{tag}.png")
    core_mask_out = os.path.join(tmp_dir, f"mask_core_{tag}.png")

    # Tunables (can be overridden via env vars)
    try:
        shrink_px = int(float(os.getenv("FABRIC_MASK_SHRINK_PX", "12") or 12))
    except Exception:
        shrink_px = 12
    shrink_px = max(0, min(64, shrink_px))

    try:
        blur_px = int(float(os.getenv("FABRIC_BG_BLUR_PX", "24") or 24))
    except Exception:
        blur_px = 24
    blur_px = max(0, min(128, blur_px))

    try:
        feather_px = int(float(os.getenv("FABRIC_MASK_FEATHER_PX", "3") or 3))
    except Exception:
        feather_px = 3
    feather_px = max(0, min(32, feather_px))

    base = Image.open(still_png).convert("RGBA")
    m = Image.open(mask_png).convert("L")

    # Ensure mask matches base size
    if m.size != base.size:
        m = m.resize(base.size, Image.NEAREST)

    # Strong binary matte first
    m_bin = m.point(lambda p: 255 if p >= 128 else 0)

    # Build core mask by eroding the white region inward
    core = m_bin
    if shrink_px > 0:
        # MinFilter acts like erosion on white-on-black mattes
        k = int(2 * shrink_px + 1)
        if k < 3:
            k = 3
        if k % 2 == 0:
            k += 1
        core = core.filter(ImageFilter.MinFilter(size=k))

    # Optional small feather (kept small so we don't pull in contaminated edge pixels)
    if feather_px > 0:
        core = core.filter(ImageFilter.GaussianBlur(radius=float(feather_px)))

    core.save(core_mask_out)

    # Fill image: blurred plate outside the core, fallback to solid
    if blur_px > 0:
        fill_img = base.filter(ImageFilter.GaussianBlur(radius=float(blur_px)))
    else:
        fill_img = Image.new("RGBA", base.size, fill_rgb + (255,))

    # Fabric input: keep base where core is white; otherwise blurred fill
    out = Image.composite(base, fill_img, core)
    out.save(out_png)

    return out_png, core_mask_out


def composite_keep_fabric_in_mask(
    still_png: str,
    fabric_mp4: str,
    mask_png: str,
    out_mp4: str,
):
    """Final composite: keep Fabric motion where mask is WHITE; keep still elsewhere.

    Mask semantics:
      - white (255): KEEP FABRIC video (speaker region)
      - black (0): KEEP STILL (frozen background / non-speaker)

    Audio is kept from fabric_mp4.
    """

    invert = os.getenv("FABRIC_MASK_INVERT", "0").strip() in ("1", "true", "True")

    mask_chain = "format=gray"
    if invert:
        mask_chain += ",negate"

    # Scale still + mask to match Fabric output size using scale2ref
    filter_graph = (
        "[0:v]format=rgba[fab0];"
        "[1:v]format=rgba[still0];"
        "[still0][fab0]scale2ref[still][fab1];"
        f"[2:v]{mask_chain}[mask0];"
        "[mask0][fab1]scale2ref[maskg][fab2];"
        "[fab2][maskg]alphamerge[fab_a];"
        "[still][fab_a]overlay=format=auto:shortest=1[outv]"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        fabric_mp4,
        "-loop",
        "1",
        "-i",
        still_png,
        "-loop",
        "1",
        "-i",
        mask_png,
        "-filter_complex",
        filter_graph,
        "-map",
        "[outv]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "24",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        out_mp4,
    ]

    subprocess.run(cmd, check=True)


def load_visuals_map(path: str = VISUALS_JSON) -> dict:
    """Load visuals.json mapping of TAG -> filename.

    Expected format:
      {"visuals": {"V1": "visual_V1.png", "WS": "visual_WS.png", ...}}
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        v = data.get("visuals", {})
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def norm_tag(tag: str) -> str:
    return (tag or "").strip().upper()


def visual_path_for_tag(vmap: dict, tag: str) -> str:
    """Return a local filename for a given tag, or empty string if not found."""
    t = norm_tag(tag)
    if not t:
        return ""
    fname = vmap.get(t)
    if not fname:
        return ""
    return fname


def load_mask_map() -> dict[str, str]:
    """Load per-shot masks from inputs/masks.

    Backend saves masks as: inputs/masks/mask_<TAG>.png
    We return a dict: TAG -> local mask path.
    """
    mdir = os.path.join("inputs", "masks")
    out: dict[str, str] = {}
    if not os.path.isdir(mdir):
        return out

    for fn in os.listdir(mdir):
        if not fn.lower().endswith(".png"):
            continue
        base = os.path.splitext(fn)[0]
        if base.lower().startswith("mask_"):
            tag = base[5:].strip().upper()
            if tag:
                out[tag] = os.path.join(mdir, fn)

    return out


def mask_path_for_tag(mask_map: dict, tag: str) -> str:
    t = norm_tag(tag)
    if not t:
        return ""
    p = mask_map.get(t, "")
    if p and os.path.exists(p):
        return p
    return ""


# --------------------------------------------------
# Main
# --------------------------------------------------

def main():
    # Key check
    if not os.environ.get("FAL_KEY") and not os.environ.get("FAL_API_KEY"):
        raise SystemExit('FAL_KEY not set. Put it in .env as FAL_KEY=...')

    # Make either name work
    if os.environ.get("FAL_KEY") and not os.environ.get("FAL_API_KEY"):
        os.environ["FAL_API_KEY"] = os.environ["FAL_KEY"]
    if os.environ.get("FAL_API_KEY") and not os.environ.get("FAL_KEY"):
        os.environ["FAL_KEY"] = os.environ["FAL_API_KEY"]

    # Optional visuals map (TAG -> filename)
    vmap = load_visuals_map(VISUALS_JSON)
    mask_map = load_mask_map()

    # Required files
    must_exist(LINES_JSON)
    must_exist(VISUALS_JSON)

    with open(LINES_JSON, "r", encoding="utf-8") as f:
        lines = json.load(f)

    if not isinstance(lines, list) or not lines:
        raise SystemExit("lines.json is empty or invalid.")

    if not vmap:
        raise SystemExit("visuals.json is missing or invalid; cannot map visual tags to images.")

    # Every line must specify a visual tag that exists in visuals.json
    for i, item in enumerate(lines, start=1):
        tag = norm_tag(item.get("visual"))
        if not tag:
            raise SystemExit(f"Line {i}: missing visual tag (e.g. '1', '2', etc).")
        path = visual_path_for_tag(vmap, tag)
        if not path or not os.path.exists(path):
            raise SystemExit(f"Line {i}: visual tag '{tag}' not found in visuals.json or file missing.")

    # Validate line 1 speaker rule
    first_speaker = (lines[0].get("speaker") or "").strip().upper()
    first_text = (lines[0].get("text") or "").strip()
    if first_speaker != INTRO_SPEAKER:
        raise SystemExit(
            f"Rule: line 1 must be speaker {INTRO_SPEAKER}.\n"
            f"Found line 1 speaker: {first_speaker!r}\n"
            f"Line 1 text: {first_text!r}"
        )

    # Validate audio for line 1 exists
    first_audio_path = "line_001.wav"
    must_exist(first_audio_path)

    intro_visual_tag = norm_tag(lines[0].get("visual"))
    intro_still_path = visual_path_for_tag(vmap, intro_visual_tag)

    # Optional per-shot mask from inputs/masks/mask_<TAG>.png
    intro_mask_path = mask_path_for_tag(mask_map, intro_visual_tag)

    if not intro_still_path or not os.path.exists(intro_still_path):
        raise SystemExit(f"Intro visual tag '{intro_visual_tag}' not found or file missing.")

    print("Uploading images (cached + retry)...")

    def get_image_url(local_path: str) -> str:
        # Global cache so the same still is never re-uploaded
        if local_path in IMAGE_URL_CACHE:
            return IMAGE_URL_CACHE[local_path]

        last_exc = None
        for attempt in range(1, 6):
            try:
                url = fal_client.upload_file(local_path)
                IMAGE_URL_CACHE[local_path] = url
                return url
            except Exception as e:
                last_exc = e
                msg = str(e)
                retriable = any(k in msg for k in ["408", "Timeout", "timed out", "429", "500", "502", "503", "504"])
                if (not retriable) or attempt >= 5:
                    raise
                sleep_s = min(30, 2 ** attempt)
                print(f"Fal upload retryable error for {os.path.basename(local_path)} (attempt {attempt}/5). Retrying in {sleep_s}s...")
                time.sleep(sleep_s)

        raise last_exc

    print("Uploading audio:", first_audio_path)
    first_audio_url = fal_client.upload_file(first_audio_path)

    print("\nINTRO: generating WS")

    ws_full_name = "00_ws_full.mp4"

    # If a mask exists, hide the masked region BEFORE Fabric so Fabric doesn't animate it,
    # then restore that region from the still AFTER Fabric.
    if intro_mask_path and os.path.exists(intro_mask_path):
        intro_name = "00_intro_ws_final.mp4"
        print("Intro mask found. Preparing Fabric input with masked region hidden...")

        fabric_input, core_mask = prepare_fabric_input_with_mask(intro_still_path, intro_mask_path, f"intro_{intro_visual_tag}")
        ws_url = get_image_url(fabric_input)

        print("Running Fabric for intro (masked input)...")
        ws_full_result = run_fabric(image_url=ws_url, audio_url=first_audio_url)
        ws_full_url = ws_full_result["video"]["url"]

        print("Downloading:", ws_full_name)
        download(ws_full_url, ws_full_name)
        print("Saved:", ws_full_name)

        print("Compositing final (keep Fabric in WHITE mask):", intro_name)
        composite_keep_fabric_in_mask(intro_still_path, ws_full_name, core_mask, intro_name)
        print("Saved:", intro_name)

        intro_name = intro_name
    else:
        print("Generating full-motion WS from Fabric (may animate both faces)")
        ws_url = get_image_url(intro_still_path)

        print("Running Fabric for intro...")
        ws_full_result = run_fabric(image_url=ws_url, audio_url=first_audio_url)
        ws_full_url = ws_full_result["video"]["url"]

        print("Downloading:", ws_full_name)
        download(ws_full_url, ws_full_name)
        print("Saved:", ws_full_name)

        intro_name = ws_full_name
        print("No intro mask found; using Fabric output as intro:", intro_name)

    time.sleep(0.25)

    # 3) Generate CU lines 2+
    for idx in range(2, len(lines) + 1):
        item = lines[idx - 1]
        speaker = (item.get("speaker") or "").strip().upper()
        text = (item.get("text") or "").strip()

        if speaker not in ("A", "B"):
            raise SystemExit(f"Line {idx}: speaker must be A or B. Got: {speaker!r}")

        audio_path = f"line_{idx:03d}.wav"
        must_exist(audio_path)

        # Choose image per-line by required visual tag
        visual_tag = norm_tag(item.get("visual"))
        chosen_local = visual_path_for_tag(vmap, visual_tag)
        if not chosen_local or not os.path.exists(chosen_local):
            raise SystemExit(f"Line {idx}: visual tag '{visual_tag}' not found or file missing.")

        out_name = f"line_{idx:03d}_{speaker}.mp4"

        print(f"\nLine {idx:03d} ({speaker}): {text}")
        print("Uploading audio:", audio_path)
        audio_url = fal_client.upload_file(audio_path)

        # If a per-shot mask exists, hide the masked region BEFORE Fabric so Fabric doesn't animate it,
        # then restore that region from the still AFTER Fabric.
        mask_path = mask_path_for_tag(mask_map, visual_tag) if visual_tag else ""
        if mask_path:
            print("Mask found. Preparing Fabric input with masked region hidden...")

            fabric_input, core_mask = prepare_fabric_input_with_mask(chosen_local, mask_path, f"{visual_tag}_{idx:03d}")
            image_url = get_image_url(fabric_input)

            print("Running Fabric (masked input, full-frame)...")
            result = run_fabric(image_url=image_url, audio_url=audio_url)
            video_url = result["video"]["url"]

            tmp_name = f"{os.path.splitext(out_name)[0]}_fabric_raw.mp4"
            print("Downloading:", tmp_name)
            download(video_url, tmp_name)
            print("Saved:", tmp_name)

            print("Compositing final (keep Fabric in WHITE mask):", out_name)
            composite_keep_fabric_in_mask(chosen_local, tmp_name, core_mask, out_name)
            print("Saved (masked restore):", out_name)

            time.sleep(0.25)
            continue

        # Fallback: no mask -> full-frame Fabric
        image_url = get_image_url(chosen_local)
        print("Running Fabric (full-frame)...")
        result = run_fabric(image_url=image_url, audio_url=audio_url)
        video_url = result["video"]["url"]

        print("Downloading:", out_name)
        download(video_url, out_name)
        print("Saved:", out_name)

        time.sleep(0.25)

    print("\nDONE: Intro WS includes line 1 (masked composite). No WS outro.")


if __name__ == "__main__":
    main()