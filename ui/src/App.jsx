import { useEffect, useMemo, useRef, useState } from "react";
import { apiFetch, LS_ELEVEN, LS_FAL } from "./api";

// Allow override via Vite env var: VITE_API_URL (set in Vercel to your Render API URL)
const API = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

function ShotBox({ label, hint, file, setFile, aspect = "16:9", warn, badge }) {
  const [thumbUrl, setThumbUrl] = useState("");

  useEffect(() => {
    if (!file) {
      setThumbUrl("");
      return;
    }
    const url = URL.createObjectURL(file);
    setThumbUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const ratio = aspect === "9:16" ? "9 / 16" : "16 / 9";

  return (
    <label
      style={{
        border: "1px solid rgba(255,255,255,0.14)",
        borderRadius: 12,
        aspectRatio: ratio,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        cursor: "pointer",
        background: "rgba(255,255,255,0.04)",
        position: "relative",
        overflow: "hidden",
        padding: 14,
        textAlign: "center",
      }}
    >
      <input
        type="file"
        accept="image/*"
        style={{ display: "none" }}
        onChange={(e) => setFile(e.target.files?.[0] || null)}
      />

      {thumbUrl ? (
        <>
          <img
            src={thumbUrl}
            alt={label}
            style={{
              position: "absolute",
              inset: 0,
              width: "100%",
              height: "100%",
              objectFit: "cover",
              opacity: 0.95,
            }}
          />
          <div
            style={{
              position: "absolute",
              inset: 0,
              background: "linear-gradient(to bottom, rgba(0,0,0,0.15), rgba(0,0,0,0.55))",
            }}
          />
          <div
            style={{
              position: "absolute",
              top: 10,
              left: 10,
              width: 34,
              height: 34,
              borderRadius: 10,
              border: "1px solid rgba(255,255,255,0.18)",
              background: "rgba(0,0,0,0.25)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontWeight: 900,
            }}
          >
            {badge}
          </div>
          <div style={{ position: "relative", width: "100%" }}>
            <div style={{ fontWeight: 900, letterSpacing: 0.4 }}>{label}</div>
            <div style={{ marginTop: 6, fontSize: 12, opacity: 0.75 }}>Image selected</div>
            <div style={{ marginTop: 6, fontSize: 12, opacity: 0.7 }}>Click to replace</div>
            {warn ? (
              <div style={{ marginTop: 8, fontSize: 12, opacity: 0.9 }}>
                ⚠ {warn}
              </div>
            ) : null}
          </div>
        </>
      ) : (
        <div style={{ width: "100%" }}>
          <div
            style={{
              width: 34,
              height: 34,
              margin: "0 auto",
              borderRadius: 10,
              border: "1px solid rgba(255,255,255,0.18)",
              background: "rgba(0,0,0,0.10)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontWeight: 900,
            }}
          >
            {badge}
          </div>
          <div style={{ fontWeight: 900, letterSpacing: 0.4, marginTop: 10 }}>{label}</div>
          <div style={{ marginTop: 8, fontSize: 12, opacity: 0.6 }}>{hint}</div>
          <div style={{ marginTop: 10, fontSize: 12, opacity: 0.5 }}>Click to upload</div>
          {warn ? (
            <div style={{ marginTop: 10, fontSize: 12, opacity: 0.9 }}>
              ⚠ {warn}
            </div>
          ) : null}
        </div>
      )}
    </label>
  );
}


export default function App() {
  const [voices, setVoices] = useState([]);
  const [voicesStatus, setVoicesStatus] = useState("Loading voices...");
  const [apiOk, setApiOk] = useState(null); // null | true | false
  const [apiHint, setApiHint] = useState("");
  const [byokRefresh, setByokRefresh] = useState(0);
  const [elevenDraft, setElevenDraft] = useState(() => {
    try {
      return localStorage.getItem(LS_ELEVEN) || "";
    } catch {
      return "";
    }
  });
  const [falDraft, setFalDraft] = useState(() => {
    try {
      return localStorage.getItem(LS_FAL) || "";
    } catch {
      return "";
    }
  });

  const DEFAULT_PERFORMANCE = 0.35;
  const [performance, setPerformance] = useState(DEFAULT_PERFORMANCE); // 0..1 (mapped to ElevenLabs style)

  const [projectName, setProjectName] = useState("scene");
  const [format, setFormat] = useState("16:9"); // "16:9" or "9:16"
  const [wsWarn, setWsWarn] = useState("");
  const [wsMaskWarn, setWsMaskWarn] = useState("");
  const [cuAWarn, setCuAWarn] = useState("");
  const [cuBWarn, setCuBWarn] = useState("");

  function validateImageOrientation(file, setWarnFn) {
    if (!file) {
      setWarnFn("");
      return;
    }

    const img = new Image();
    const url = URL.createObjectURL(file);

    img.onload = () => {
      const w = img.naturalWidth;
      const h = img.naturalHeight;
      URL.revokeObjectURL(url);

      const isPortrait = h > w;
      const wantPortrait = format === "9:16";

      if (wantPortrait !== isPortrait) {
        setWarnFn(
          `This looks ${isPortrait ? "9:16" : "16:9"}. Format is set to ${format}. It will still work, but preview framing may feel off.`
        );
      } else {
        setWarnFn("");
      }
    };

    img.onerror = () => {
      URL.revokeObjectURL(url);
      setWarnFn("Could not read image dimensions.");
    };

    img.src = url;
  }

  const [ws, setWs] = useState(null);
  const [wsMask, setWsMask] = useState(null);
  const [cuA, setCuA] = useState(null);
  const [cuB, setCuB] = useState(null);

  const [shots, setShots] = useState([
    { tag: "1", image: null, mask: null, mask_invert: false, voice_id: "" },
    { tag: "2", image: null, mask: null, mask_invert: false, voice_id: "" },
    { tag: "3", image: null, mask: null, mask_invert: false, voice_id: "" },
  ]);

  function normTag(t) {
    return String(t || "").trim().toUpperCase();
  }

  function extractScriptVisualTags(text) {
    // Finds prefixes like "V1:" or "CU_A:" on each line.
    // Ignores classic speaker-only prefixes A:/B:.
    const tags = new Set();
    const lines = String(text || "").split("\n");
    for (const raw of lines) {
      const ln = raw.trim();
      if (!ln) continue;
      const i = ln.indexOf(":");
      if (i === -1) continue;
      const left = normTag(ln.slice(0, i));
      if (!left) continue;
      if (left === "A" || left === "B") continue;
      tags.add(left);
    }
    return Array.from(tags);
  }

  useEffect(() => {
    validateImageOrientation(ws, setWsWarn);
  }, [ws, format]);

  useEffect(() => {
    validateImageOrientation(wsMask, setWsMaskWarn);
  }, [wsMask, format]);

  useEffect(() => {
    validateImageOrientation(cuA, setCuAWarn);
  }, [cuA, format]);

  useEffect(() => {
    validateImageOrientation(cuB, setCuBWarn);
  }, [cuB, format]);

  // Derive ws/wsMask/cuA/cuB from shots
  useEffect(() => {
    // Keep existing backend requirements satisfied using the first shots
    setWs(shots[0]?.image || null);
    setWsMask(shots[0]?.mask || null);
    setCuA(shots[1]?.image || null);
    setCuB(shots[2]?.image || null);
  }, [shots]);

  const [script, setScript] = useState("");
  const [scriptLines, setScriptLines] = useState([
    { shot_tag: "1", text: "" },
    { shot_tag: "2", text: "" },
    { shot_tag: "3", text: "" },
  ]);
  const [showScriptImport, setShowScriptImport] = useState(false);
  const [inputMode, setInputMode] = useState("script"); // "script" | "audio"
  const [renderer, setRenderer] = useState("fabric"); // "fabric" | "ltx"
  const [ltxPrompt, setLtxPrompt] = useState("");
  const [useNativeAudio, setUseNativeAudio] = useState(false);
  const [useDialogueMode, setUseDialogueMode] = useState(true);
  const [shotThumbUrls, setShotThumbUrls] = useState({}); // { [tag]: objectUrl }
  const [audioPreviewUrls, setAudioPreviewUrls] = useState({}); // { [lineIndex]: objectUrl }
  const audioPlayersRef = useRef(new Map()); // Map<number, HTMLAudioElement>
  const [playingAudioIdx, setPlayingAudioIdx] = useState(null); // number | null
  const [audioLines, setAudioLines] = useState([
    { shot_tag: "1", file: null },
    { shot_tag: "2", file: null },
    { shot_tag: "3", file: null },
  ]);

  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [outputs, setOutputs] = useState([]);
  const [previewUrl, setPreviewUrl] = useState("");
  const [showPreview, setShowPreview] = useState(false);

  function addScriptLine() {
    setScriptLines((prev) => {
      const nextTag = String(Math.min(prev.length + 1, shots.length));
      return [...prev, { shot_tag: nextTag, text: "" }];
    });
  }

  function removeScriptLine(idx) {
    setScriptLines((prev) => prev.filter((_, i) => i !== idx));
  }

  function updateScriptLine(idx, patch) {
    setScriptLines((prev) => prev.map((l, i) => (i === idx ? { ...l, ...patch } : l)));
  }

  function applyEmotionTag(idx, tag) {
    const t = String(tag || "").trim();
    if (!t) return;

    setScriptLines((prev) =>
      prev.map((l, i) => {
        if (i !== idx) return l;
        const raw = String(l.text || "");
        const trimmed = raw.trimStart();
        const newTag = `[${t}]`;

        // If the line already starts with a bracket tag, replace it.
        if (trimmed.startsWith("[") && trimmed.includes("]")) {
          const end = trimmed.indexOf("]");
          const rest = trimmed.slice(end + 1).trimStart();
          return { ...l, text: `${newTag} ${rest}`.trimEnd() };
        }

        // Otherwise, prepend the tag.
        if (!trimmed) {
          return { ...l, text: `${newTag} ` };
        }
        return { ...l, text: `${newTag} ${trimmed}` };
      })
    );
  }

  function buildScriptFromLines(lines) {
    return (lines || [])
      .map((l) => {
        const tag = normTag(l.shot_tag) || "1";
        const txt = String(l.text || "").trim();
        if (!txt) return "";
        return `${tag}: ${txt}`;
      })
      .filter(Boolean)
      .join("\n");
  }

  function parseScriptToLines(text) {
    const out = [];
    const rows = String(text || "").split("\n");
    for (const raw of rows) {
      const ln = raw.trim();
      if (!ln) continue;
      const i = ln.indexOf(":");
      if (i === -1) {
        // If no tag prefix, just treat it as Shot 1
        out.push({ shot_tag: "1", text: ln });
        continue;
      }
      const left = normTag(ln.slice(0, i));
      const right = ln.slice(i + 1).trim();
      // If prefix is A/B, keep text but default to Shot 1 for visuals
      if (left === "A" || left === "B") {
        out.push({ shot_tag: "1", text: right || "" });
      } else {
        out.push({ shot_tag: left || "1", text: right || "" });
      }
    }
    return out.length ? out : [{ shot_tag: "1", text: "" }];
  }

  function addAudioLine() {
    setAudioLines((prev) => {
      const nextTag = String(Math.min(prev.length + 1, shots.length));
      return [...prev, { shot_tag: nextTag, file: null }];
    });
  }

  function removeAudioLine(idx) {
    setAudioLines((prev) => prev.filter((_, i) => i !== idx));
  }

  function updateAudioLine(idx, patch) {
    setAudioLines((prev) => prev.map((l, i) => (i === idx ? { ...l, ...patch } : l)));
  }

  useEffect(() => {
    if (inputMode !== "script") return;
    const next = buildScriptFromLines(scriptLines);
    setScript(next);
  }, [inputMode, scriptLines]);

  const scriptReady = useMemo(() => {
    if (inputMode !== "script") return true;
    if (!scriptLines.length) return false;
    const shotMap = new Map(shots.map((s) => [normTag(s.tag), s]));
    return scriptLines.every((l) => {
      const txt = String(l.text || "").trim();
      if (!txt) return false;
      const t = normTag(l.shot_tag);
      if (!t) return false;
      const s = shotMap.get(t);
      if (!s || !s.image) return false;
      if (!s.voice_id) return false;
      return true;
    });
  }, [inputMode, scriptLines, shots]);

  const audioReady = useMemo(() => {
    if (inputMode !== "audio") return true;
    if (!audioLines.length) return false;
    const shotMap = new Map(shots.map((s) => [normTag(s.tag), s]));
    return audioLines.every((l) => {
      if (!l.file) return false;
      const t = normTag(l.shot_tag);
      if (!t) return false;
      const s = shotMap.get(t);
      if (!s || !s.image) return false; // must point to an uploaded shot
      // Only require voices if we're converting via STS.
      if (!useNativeAudio && !s.voice_id) return false;
      return true;
    });
  }, [inputMode, audioLines, shots, useNativeAudio]);

  useEffect(() => {
    // Build a tag -> objectURL map for shot thumbnails
    const next = {};
    for (const s of shots) {
      if (s?.tag && s?.image instanceof File) {
        next[String(s.tag)] = URL.createObjectURL(s.image);
      }
    }

    // Revoke previous URLs no longer used
    setShotThumbUrls((prev) => {
      for (const k of Object.keys(prev || {})) {
        if (!next[k] || next[k] !== prev[k]) {
          try {
            URL.revokeObjectURL(prev[k]);
          } catch {}
        }
      }
      return next;
    });

    return () => {
      try {
        for (const k of Object.keys(next)) {
          URL.revokeObjectURL(next[k]);
        }
      } catch {}
    };
  }, [shots]);

  useEffect(() => {
    // Build an index -> objectURL map for audio previews
    const next = {};
    audioLines.forEach((l, idx) => {
      if (l?.file instanceof File) {
        next[idx] = URL.createObjectURL(l.file);
      }
    });

    setAudioPreviewUrls((prev) => {
      for (const k of Object.keys(prev || {})) {
        const ki = Number(k);
        if (!Object.prototype.hasOwnProperty.call(next, ki) || next[ki] !== prev[k]) {
          try {
            URL.revokeObjectURL(prev[k]);
          } catch {}
        }
      }
      return next;
    });

    return () => {
      try {
        for (const k of Object.keys(next)) {
          URL.revokeObjectURL(next[k]);
        }
      } catch {}
    };
  }, [audioLines]);

  useEffect(() => {
    // Stop any playing preview when switching modes or changing audio list
    if (playingAudioIdx !== null) {
      const p = audioPlayersRef.current.get(playingAudioIdx);
      if (p) {
        try {
          p.pause();
          p.currentTime = 0;
        } catch {}
      }
      setPlayingAudioIdx(null);
    }
  }, [inputMode, audioLines]);

  const canGenerate = useMemo(() => {
    const baseOk =
      shots.length >= 3 &&
      !!shots[0]?.image &&
      !!shots[1]?.image &&
      !!shots[2]?.image &&
      projectName.trim().length > 0 &&
      !busy;

    if (!baseOk) return false;

    if (inputMode === "script") {
      return scriptReady;
    }

    return audioReady;
  }, [shots, projectName, busy, inputMode, audioReady, scriptReady, scriptLines]);

  // Visual tag coverage warnings
  const scriptVisualTags = useMemo(() => {
    if (inputMode !== "script") return [];
    return extractScriptVisualTags(script);
  }, [script, inputMode]);

  const uploadedVisualTags = useMemo(() => {
    return shots
      .map((s) => ({ tag: normTag(s.tag), hasFile: !!s.image }))
      .filter((s) => s.tag)
      .filter((s) => s.hasFile)
      .map((s) => s.tag);
  }, [shots]);

  const missingVisualTags = useMemo(() => {
    if (inputMode !== "script") return [];
    if (!scriptVisualTags.length) return [];
    const uploaded = new Set(uploadedVisualTags);
    return scriptVisualTags.filter((t) => !uploaded.has(t));
  }, [scriptVisualTags, uploadedVisualTags, inputMode]);

  useEffect(() => {
    async function loadVoices() {
      setVoicesStatus("Loading voices...");
      setApiHint("");

      // 1) Quick health check (best-effort)
      try {
        const healthRes = await apiFetch(`${API}/api/health`);
        if (healthRes.ok) {
          setApiOk(true);
        } else {
          setApiOk(false);
          const t = await healthRes.text();
          setApiHint(t ? `Health check responded, but not OK: ${t}` : "Health check responded, but not OK.");
        }
      } catch (e) {
        setApiOk(false);
        setApiHint("Cannot reach backend. Check VITE_API_URL / API URL and that the server is running.");
      }

      // 2) Voices fetch
      try {
        const res = await apiFetch(`${API}/api/voices`);
        if (!res.ok) {
          const t = await res.text();
          setVoices([]);
          setVoicesStatus(`Failed to load voices (${res.status}). ${t || ""}`.trim());
          return;
        }
        const data = await res.json();
        const list = data.voices || [];
        setVoices(list);
        setVoicesStatus(list.length ? "" : "No voices found.");
      } catch (e) {
        setVoices([]);
        setVoicesStatus("Failed to load voices. Check backend and CORS.");
      }
    }
    loadVoices();
  }, [byokRefresh]);
  const toggleAudioPreview = (idx) => {
    const url = audioPreviewUrls[idx];
    if (!url) return;

    // Pause any currently playing audio
    if (playingAudioIdx !== null && playingAudioIdx !== idx) {
      const prev = audioPlayersRef.current.get(playingAudioIdx);
      if (prev) {
        try {
          prev.pause();
          prev.currentTime = 0;
        } catch {}
      }
    }

    // Get or create player
    let player = audioPlayersRef.current.get(idx);
    if (!player) {
      player = new Audio(url);
      audioPlayersRef.current.set(idx, player);
      player.addEventListener("ended", () => {
        setPlayingAudioIdx((cur) => (cur === idx ? null : cur));
      });
    }

    // Keep src current if file changed
    if (player.src !== url) {
      try {
        player.pause();
        player.currentTime = 0;
      } catch {}
      player.src = url;
    }

    // Toggle
    if (playingAudioIdx === idx) {
      try {
        player.pause();
        player.currentTime = 0;
      } catch {}
      setPlayingAudioIdx(null);
    } else {
      player
        .play()
        .then(() => setPlayingAudioIdx(idx))
        .catch(() => {
          setPlayingAudioIdx(null);
        });
    }
  };

  async function invertImageFile(file) {
    const bmp = await createImageBitmap(file);
    const canvas = document.createElement("canvas");
    canvas.width = bmp.width;
    canvas.height = bmp.height;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Canvas not supported");
  
    ctx.drawImage(bmp, 0, 0);
  
    const imgData = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const d = imgData.data;
  
    for (let i = 0; i < d.length; i += 4) {
      d[i] = 255 - d[i];
      d[i + 1] = 255 - d[i + 1];
      d[i + 2] = 255 - d[i + 2];
    }
  
    ctx.putImageData(imgData, 0, 0);
  
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
    if (!blob) throw new Error("Failed to create PNG");
  
    return new File([blob], file.name.replace(/\.[^.]+$/, "") + "_inv.png", { type: "image/png" });
  }
  async function generate() {
    setShowPreview(false);
    setPreviewUrl("");
    setOutputs([]);

    if (shots.length < 3 || !shots[0]?.image || !shots[1]?.image || !shots[2]?.image) {
      setStatus("Add and upload at least 3 shots (1, 2, 3) with images.");
      return;
    }
    if (inputMode === "script") {
      if (!scriptReady) {
        setStatus("Add at least one Script line with text, a Shot selection, and a selected voice.");
        return;
      }
    } else {
      if (!audioReady) {
        setStatus("Upload audio for each line and assign a Shot number with a selected voice.");
        return;
      }
    }

    // Multi-voice: require a voice on every uploaded shot.
    const uploadedShots = shots.filter((s) => !!s.image);
    if (uploadedShots.length === 0) {
      setStatus("Upload at least one shot.");
      return;
    }

    // Only require voices if not using native audio
    if (!(inputMode === "audio" && useNativeAudio)) {
      const missingVoices = uploadedShots.filter((s) => !s.voice_id);
      if (missingVoices.length > 0) {
        setStatus("Select a voice for each uploaded shot.");
        return;
      }
    }

    // Back-compat: endpoints still accept voice_a/voice_b. Use the first shots as defaults.
    const derivedVoiceA = uploadedShots[0].voice_id;
    const derivedVoiceB = uploadedShots[1]?.voice_id || derivedVoiceA;

    try {
      setBusy(true);
      let projectDir = "";

      const effectiveRenderer = inputMode === "audio" ? renderer : "fabric";

      if (inputMode === "script") {
        setStatus("Step 1/4: Creating project...");

        const ttsRes = await apiFetch(`${API}/api/project_from_script`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            script,
            voice_a: derivedVoiceA,
            voice_b: derivedVoiceB,
            project_name: projectName,
            performance,
          }),
        });

        if (!ttsRes.ok) {
          const t = await ttsRes.text();
          setStatus(`Audio error: ${t}`);
          return;
        }

        const ttsData = await ttsRes.json();
        projectDir = ttsData.project_dir;
      } else {
        setStatus("Step 1/3: Converting uploaded audio...");

        const fdAudio = new FormData();
        fdAudio.append("project_name", projectName);
        fdAudio.append("voice_a", derivedVoiceA);
        fdAudio.append("voice_b", derivedVoiceB);
        fdAudio.append("performance", String(performance));
        fdAudio.append("use_native_audio", useNativeAudio ? "true" : "false");

        const shotByTag = new Map(shots.map((s) => [normTag(s.tag), s]));

        // For STS, voice selection is driven by voices_json. Keep speakers_json for compatibility.
        const speakers = audioLines.map(() => "A");
        const visualTags = audioLines.map((l) => normTag(l.shot_tag));
        const voicesByLine = audioLines.map((l) => {
          const t = normTag(l.shot_tag);
          const s = shotByTag.get(t);
          return s?.voice_id || "";
        });

        fdAudio.append("speakers_json", JSON.stringify(speakers));
        fdAudio.append("visual_tags_json", JSON.stringify(visualTags));
        fdAudio.append("voices_json", JSON.stringify(voicesByLine));

        audioLines.forEach((l) => {
          fdAudio.append("audios", l.file);
        });

        const stsRes = await apiFetch(`${API}/api/sts`, {
          method: "POST",
          body: fdAudio,
        });

        if (!stsRes.ok) {
          const t = await stsRes.text();
          setStatus(`Audio error: ${t}`);
          return;
        }

        const stsData = await stsRes.json();
        projectDir = stsData.project_dir;
      }

      setStatus(inputMode === "script" ? "Step 2/4: Uploading images..." : "Step 2/3: Uploading images...");

      const wsFile = shots[0]?.image || null;
      const cuAFile = shots[1]?.image || null;
      const cuBFile = shots[2]?.image || null;
      
      const maskFileRaw = shots[0]?.mask || null;
      const maskFile =
        maskFileRaw && shots[0]?.mask_invert ? await invertImageFile(maskFileRaw) : maskFileRaw;
      
      const fd = new FormData();
      fd.append("project_dir", projectDir);
      fd.append("ws", wsFile);
      if (maskFile) fd.append("ws_mask", maskFile);
      fd.append("cu_a", cuAFile);
      fd.append("cu_b", cuBFile);

      const upRes = await apiFetch(`${API}/api/upload_inputs`, {
        method: "POST",
        body: fd,
      });

      if (!upRes.ok) {
        const t = await upRes.text();
        setStatus(`Upload error: ${t}`);
        return;
      }

      // Optional: upload tagged visuals (for per-line visual control)
      const tagged = shots
        .map((s) => ({
          tag: normTag(s.tag),
          file: s.image,
          voice_id: s.voice_id || "",
          // Legacy speaker retained; multi-voice uses voices_json.
          speaker: "A",
        }))
        .filter((v) => v.tag && v.file);

      if (tagged.length > 0) {
        setStatus(inputMode === "script" ? "Step 2/4: Uploading visual slots..." : "Step 2/3: Uploading visual slots...");

        const fdV = new FormData();
        fdV.append("project_dir", projectDir);
        fdV.append("tags_json", JSON.stringify(tagged.map((t) => t.tag)));
        fdV.append("speakers_json", JSON.stringify(tagged.map((t) => t.speaker)));
        fdV.append("voices_json", JSON.stringify(tagged.map((t) => t.voice_id)));
        tagged.forEach((t) => fdV.append("files", t.file));

        const visRes = await apiFetch(`${API}/api/upload_visuals`, {
          method: "POST",
          body: fdV,
        });

        if (!visRes.ok) {
          const t = await visRes.text();
          setStatus(`Visual slots upload error: ${t}`);
          return;
        }
      }

      // Optional: upload tagged masks per shot (applied per-line by visual tag)
      const masked = shots
        .map((s) => ({ tag: normTag(s.tag), file: s.mask, invert: !!s.mask_invert }))
        .filter((m) => m.tag && m.file);

      if (masked.length > 0) {
        setStatus(inputMode === "script" ? "Step 2/4: Uploading masks..." : "Step 2/3: Uploading masks...");

        const fdM = new FormData();
        fdM.append("project_dir", projectDir);
        fdM.append("tags_json", JSON.stringify(masked.map((m) => m.tag)));

        // Pre-invert masks client-side if requested
        for (const m of masked) {
          const f = m.invert ? await invertImageFile(m.file) : m.file;
          fdM.append("files", f);
        }

        const maskRes = await apiFetch(`${API}/api/upload_masks`, {
          method: "POST",
          body: fdM,
        });

        if (!maskRes.ok) {
          const t = await maskRes.text();
          setStatus(`Mask upload error: ${t}`);
          return;
        }
      }

      // Script mode: generate audio AFTER visuals are uploaded, using visuals.json speaker mapping
      if (inputMode === "script") {
        setStatus("Step 3/4: Generating audio...");
        const audioRes = await apiFetch(`${API}/api/generate_audio`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            project_dir: projectDir,
            voice_a: derivedVoiceA,
            voice_b: derivedVoiceB,
            performance,
            use_dialogue_mode: useDialogueMode,
          }),
        });

        if (!audioRes.ok) {
          const t = await audioRes.text();
          setStatus(`Audio generation error: ${t}`);
          return;
        }

        // Read backend debug flags so we can confirm dialogue mode without DevTools
        let audioDebug = null;
        try {
          audioDebug = await audioRes.json();
        } catch {
          audioDebug = null;
        }

        const dbgRequested = audioDebug?.dialogue_mode_requested;
        const dbgUsed = audioDebug?.dialogue_mode_used;
        const dbgReason = audioDebug?.dialogue_fallback_reason;

        const dbgLine =
          dbgRequested === undefined
            ? "Dialogue debug: (no flags returned)"
            : `Dialogue requested: ${dbgRequested} | Used: ${dbgUsed} | ${dbgUsed ? "OK" : `Fallback: ${dbgReason || "unknown"}`}`;

        setStatus(
          `Step 4/4: Rendering ${effectiveRenderer === "ltx" ? "LTX" : "Fabric"} clips...  [${dbgLine}]`
        );
      } else {
        setStatus(`Step 3/3: Rendering ${effectiveRenderer === "ltx" ? "LTX" : "Fabric"} clips...`);
      }

      const renderRes = await apiFetch(`${API}/api/render`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_dir: projectDir,
          renderer: effectiveRenderer,
          ltx_prompt: effectiveRenderer === "ltx" ? ltxPrompt : "",
        }),
      });

      if (!renderRes.ok) {
        const t = await renderRes.text();
        setStatus(`Render error: ${t}`);
        return;
      }

      const renderData = await renderRes.json();

      const outDir = renderData.outputs_dir;
      const files = renderData.outputs || [];
      const prev = renderData.preview || "";

      const toUrl = (name) => {
        const parts = (outDir || "").replaceAll("\\\\", "/").split("/");
        const idx = parts.lastIndexOf("projects");
        if (idx === -1 || idx + 1 >= parts.length) return "";
        const proj = parts[idx + 1];
        return `${API}/projects/${proj}/outputs/${name}`;
      };

      setOutputs(files.map((n) => ({ name: n, url: toUrl(n) })));
      setPreviewUrl(prev ? toUrl(prev) : "");

      setStatus("Done. Clips are ready.");
    } catch (e) {
      setStatus("Unexpected error. Check backend.log.");
    } finally {
      setBusy(false);
    }
  }

  // Bleed background to browser edges
  useEffect(() => {
    // Make the app background bleed to browser edges.
    const prevMargin = document.body.style.margin;
    const prevBg = document.body.style.background;
    const prevColor = document.body.style.color;

    document.body.style.margin = "0";
    document.body.style.background = "#1f1f1f";
    document.body.style.color = "#eaeaea";

    return () => {
      document.body.style.margin = prevMargin;
      document.body.style.background = prevBg;
      document.body.style.color = prevColor;
    };
  }, []);

  return (
    <div
      style={{
        minHeight: "100vh",
        width: "100%",
        background: "#1f1f1f",
        color: "#eaeaea",
        padding: 0,
        overflowX: "hidden",
        fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
      }}
    >
      <div
        style={{
          maxWidth: 1280,
          margin: "0 auto",
          padding: 32,
          boxSizing: "border-box",
        }}
      >
      <div style={{ display: "flex", alignItems: "baseline", gap: 16, flexWrap: "wrap" }}>
        <h1 style={{ fontSize: 44, letterSpacing: 1.5, margin: 0, fontWeight: 950 }}>Episode Builder</h1>
      </div>

      <div
        style={{
          marginTop: 20,
          padding: 16,
          borderRadius: 12,
          border: "1px solid rgba(255,255,255,0.12)",
          background: "rgba(255,255,255,0.04)",
          maxWidth: 720,
        }}
      >
        <div style={{ fontWeight: 900, fontSize: 15, marginBottom: 8 }}>Your API keys (BYOK)</div>
        <div style={{ fontSize: 12, opacity: 0.7, marginBottom: 12, lineHeight: 1.45 }}>
          Keys stay in this browser (localStorage) and are sent to your backend over HTTPS only. For local dev,
          the backend can use <code style={{ opacity: 0.9 }}>backend/.env</code> instead.
        </div>
        <div style={{ display: "grid", gap: 10 }}>
          <label style={{ display: "grid", gap: 4, fontSize: 12 }}>
            <span style={{ opacity: 0.8 }}>ElevenLabs API key</span>
            <input
              type="password"
              autoComplete="off"
              value={elevenDraft}
              onChange={(e) => setElevenDraft(e.target.value)}
              placeholder="xi-api-key…"
              style={{
                padding: 10,
                borderRadius: 8,
                border: "1px solid rgba(255,255,255,0.14)",
                background: "rgba(0,0,0,0.25)",
                color: "#eaeaea",
              }}
            />
          </label>
          <label style={{ display: "grid", gap: 4, fontSize: 12 }}>
            <span style={{ opacity: 0.8 }}>FAL key (Fabric / LTX)</span>
            <input
              type="password"
              autoComplete="off"
              value={falDraft}
              onChange={(e) => setFalDraft(e.target.value)}
              placeholder="FAL_KEY…"
              style={{
                padding: 10,
                borderRadius: 8,
                border: "1px solid rgba(255,255,255,0.14)",
                background: "rgba(0,0,0,0.25)",
                color: "#eaeaea",
              }}
            />
          </label>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <button
              type="button"
              onClick={() => {
                try {
                  localStorage.setItem(LS_ELEVEN, elevenDraft.trim());
                  localStorage.setItem(LS_FAL, falDraft.trim());
                } catch {}
                setByokRefresh((n) => n + 1);
              }}
              style={{
                padding: "8px 14px",
                borderRadius: 10,
                border: "1px solid rgba(255,255,255,0.18)",
                background: "rgba(255,255,255,0.1)",
                color: "#eaeaea",
                cursor: "pointer",
                fontWeight: 800,
              }}
            >
              Save keys & reload voices
            </button>
            <button
              type="button"
              onClick={() => {
                try {
                  localStorage.removeItem(LS_ELEVEN);
                  localStorage.removeItem(LS_FAL);
                } catch {}
                setElevenDraft("");
                setFalDraft("");
                setByokRefresh((n) => n + 1);
              }}
              style={{
                padding: "8px 14px",
                borderRadius: 10,
                border: "1px solid rgba(255,255,255,0.12)",
                background: "transparent",
                color: "#eaeaea",
                cursor: "pointer",
                fontWeight: 700,
                opacity: 0.85,
              }}
            >
              Clear
            </button>
          </div>
        </div>
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 12,
          flexWrap: "wrap",
          marginTop: 18,
        }}
      >
        <div>
          <div style={{ fontWeight: 900, fontSize: 18 }}>Shots</div>
          <div style={{ fontSize: 12, opacity: 0.65, marginTop: 4 }}>
            Upload shots 1, 2, 3... Assign a voice per shot (unlimited).
          </div>
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <button
            onClick={() =>
              setShots((prev) => [
                ...prev,
                { tag: String(prev.length + 1), image: null, mask: null, mask_invert: false, voice_id: "" },
              ])
            }
            style={{
              padding: "10px 14px",
              borderRadius: 12,
              border: "1px solid rgba(255,255,255,0.14)",
              background: "rgba(255,255,255,0.06)",
              color: "#eaeaea",
              cursor: "pointer",
              fontWeight: 900,
            }}
          >
            Add shot
          </button>

          <button
            onClick={() => setShots((prev) => (prev.length > 1 ? prev.slice(0, -1) : prev))}
            disabled={shots.length <= 1}
            style={{
              padding: "10px 14px",
              borderRadius: 12,
              border: "1px solid rgba(255,255,255,0.14)",
              background: "rgba(255,255,255,0.03)",
              color: "#eaeaea",
              cursor: shots.length <= 1 ? "not-allowed" : "pointer",
              opacity: shots.length <= 1 ? 0.4 : 0.9,
              fontWeight: 900,
            }}
          >
            Remove last
          </button>
        </div>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns:
            format === "9:16" ? "repeat(4, minmax(160px, 1fr))" : "repeat(4, minmax(180px, 1fr))",
          gap: 16,
          marginTop: 14,
        }}
      >
        {shots.map((s, idx) => (
          <div key={s.tag} style={{ display: "grid", gap: 10 }}>
            <ShotBox
              label={`Shot ${idx + 1}`}
              hint="Click to upload"
              file={s.image}
              setFile={(f) =>
                setShots((prev) => prev.map((p, i) => (i === idx ? { ...p, image: f } : p)))
              }
              aspect={format}
              warn={idx === 0 ? wsWarn : idx === 1 ? cuAWarn : idx === 2 ? cuBWarn : ""}
              badge={s.tag}
            />

            <div style={{ display: "grid", gap: 8 }}>
              <select
                disabled={inputMode === "audio" && useNativeAudio}
                value={s.voice_id || ""}
                onChange={(e) =>
                  setShots((prev) => prev.map((p, i) => (i === idx ? { ...p, voice_id: e.target.value } : p)))
                }
                style={{
                  width: "100%",
                  padding: 10,
                  borderRadius: 10,
                  border: "1px solid rgba(255,255,255,0.14)",
                  background: "rgba(255,255,255,0.04)",
                  color: "#eaeaea",
                  fontWeight: 900,
                  opacity: inputMode === "audio" && useNativeAudio ? 0.45 : 1,
                  cursor: inputMode === "audio" && useNativeAudio ? "not-allowed" : "pointer",
                }}
              >
                <option value="">Select voice</option>
                {voices.map((v) => (
                  <option key={v.voice_id} value={v.voice_id}>
                    {v.name}
                  </option>
                ))}
              </select>

              <label
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 10,
                  padding: "10px 12px",
                  borderRadius: 12,
                  border: "1px solid rgba(255,255,255,0.14)",
                  background: "rgba(255,255,255,0.03)",
                  cursor: "pointer",
                  fontWeight: 900,
                  opacity: 0.92,
                }}
              >
                <input
                  type="file"
                  accept="image/*"
                  style={{ display: "none" }}
                  onChange={(e) =>
                    setShots((prev) =>
                      prev.map((p, i) => (i === idx ? { ...p, mask: e.target.files?.[0] || null } : p))
                    )
                  }
                />
                {s.mask ? "Replace mask" : "Upload mask (optional)"}
              </label>

              {s.mask ? (
                <label style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 12, opacity: 0.9 }}>
                  <input
                    type="checkbox"
                    checked={!!s.mask_invert}
                    onChange={(e) =>
                      setShots((prev) =>
                        prev.map((p, i) => (i === idx ? { ...p, mask_invert: e.target.checked } : p))
                      )
                    }
                  />
                  Invert mask
                </label>
              ) : null}
            </div>
          </div>
        ))}
      </div>

      <div style={{ display: "flex", gap: 16, marginTop: 18, flexWrap: "wrap" }}>
        <div style={{ minWidth: 220 }}>
          <div style={{ fontWeight: 800, marginBottom: 6 }}>Format</div>
          <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
            <label style={{ display: "flex", alignItems: "center", gap: 8, opacity: 0.9 }}>
              <input
                type="radio"
                name="format"
                value="16:9"
                checked={format === "16:9"}
                onChange={() => setFormat("16:9")}
              />
              16:9
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: 8, opacity: 0.9 }}>
              <input
                type="radio"
                name="format"
                value="9:16"
                checked={format === "9:16"}
                onChange={() => setFormat("9:16")}
              />
              9:16
            </label>
          </div>
        </div>

        <div style={{ minWidth: 220 }}>
          <div style={{ fontWeight: 800, marginBottom: 6 }}>Project name</div>
          <input
            value={projectName}
            onChange={(e) => setProjectName(e.target.value)}
            style={{
              width: 260,
              padding: 10,
              borderRadius: 10,
              border: "1px solid rgba(255,255,255,0.14)",
              background: "rgba(255,255,255,0.04)",
              color: "#eaeaea",
            }}
          />
        </div>


        <div style={{ minWidth: 260 }}>
          <div style={{ fontWeight: 800, marginBottom: 6 }}>Performance</div>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={performance}
            onChange={(e) => setPerformance(parseFloat(e.target.value))}
            style={{ width: 260 }}
          />
          <div style={{ fontSize: 12, opacity: 0.65, marginTop: 6 }}>
            {performance.toFixed(2)} (0.35 = neutral, low = restrained, high = expressive)
          </div>
        </div>

      </div>

      <div style={{ marginTop: 10, display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <div style={{ opacity: 0.8, fontSize: 13 }}>
          Backend: {apiOk === null ? "checking..." : apiOk ? "connected" : "not reachable"}
          {apiHint ? ` (${apiHint})` : ""}
        </div>
      </div>

      {voicesStatus && <div style={{ marginTop: 8, opacity: 0.75, fontSize: 13 }}>{voicesStatus}</div>}

      <div style={{ marginTop: 20, display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <div style={{ fontWeight: 900, fontSize: 18 }}>Input</div>
        <div
          style={{
            display: "inline-flex",
            border: "1px solid rgba(255,255,255,0.14)",
            borderRadius: 999,
            overflow: "hidden",
            background: "rgba(255,255,255,0.04)",
          }}
        >
          <button
            onClick={() => {
              setInputMode("script");
              setRenderer("fabric");
            }}
            style={{
              padding: "8px 12px",
              border: "none",
              background: inputMode === "script" ? "rgba(255,255,255,0.12)" : "transparent",
              color: "#eaeaea",
              cursor: "pointer",
              fontWeight: 900,
            }}
          >
            Script
          </button>
          <button
            onClick={() => setInputMode("audio")}
            style={{
              padding: "8px 12px",
              border: "none",
              background: inputMode === "audio" ? "rgba(255,255,255,0.12)" : "transparent",
              color: "#eaeaea",
              cursor: "pointer",
              fontWeight: 900,
            }}
          >
            Audio
          </button>
        </div>
      </div>

      {inputMode === "audio" && (
        <>
          <div style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <div style={{ fontWeight: 900, fontSize: 18 }}>Renderer</div>
            <div
              style={{
                display: "inline-flex",
                border: "1px solid rgba(255,255,255,0.14)",
                borderRadius: 999,
                overflow: "hidden",
                background: "rgba(255,255,255,0.04)",
              }}
            >
              <button
                onClick={() => setRenderer("fabric")}
                style={{
                  padding: "8px 12px",
                  border: "none",
                  background: renderer === "fabric" ? "rgba(255,255,255,0.12)" : "transparent",
                  color: "#eaeaea",
                  cursor: "pointer",
                  fontWeight: 900,
                }}
              >
                Fabric
              </button>
              <button
                onClick={() => setRenderer("ltx")}
                style={{
                  padding: "8px 12px",
                  border: "none",
                  background: renderer === "ltx" ? "rgba(255,255,255,0.12)" : "transparent",
                  color: "#eaeaea",
                  cursor: "pointer",
                  fontWeight: 900,
                }}
              >
                LTX
              </button>
            </div>

            <div style={{ fontSize: 12, opacity: 0.75 }}>
              {renderer === "fabric" ? (
                "Best for predictable lip sync"
              ) : (
                <>
                  <span style={{ fontWeight: 900, color: "#ffd36a" }}>Experimental:</span>{" "}
                  Best for cinematic motion. More variable. Per line render.
                </>
              )}
            </div>
          </div>

          {renderer === "ltx" && (
            <div style={{ marginTop: 10 }}>
              <div style={{ fontWeight: 800, marginBottom: 6 }}>LTX prompt (optional)</div>
              <textarea
                rows={3}
                value={ltxPrompt}
                onChange={(e) => setLtxPrompt(e.target.value)}
                placeholder="Optional. Add style + motion notes (camera is forced static server-side). Example: soft daylight, subtle facial motion, still background."
                style={{
                  width: "100%",
                  padding: 12,
                  borderRadius: 12,
                  border: "1px solid rgba(255,255,255,0.14)",
                  background: "rgba(255,255,255,0.04)",
                  color: "#eaeaea",
                  fontSize: 14,
                  lineHeight: 1.35,
                }}
              />
              <div style={{ fontSize: 12, opacity: 0.6, marginTop: 6 }}>
                Tip: keep it short. Focus on subtle facial motion and a still background.
              </div>
            </div>
          )}
        </>
      )}

      {inputMode === "script" ? (
          <div
            style={{
              marginTop: 10,
              padding: 14,
              borderRadius: 12,
              border: "1px solid rgba(255,255,255,0.14)",
              background: "rgba(255,255,255,0.04)",
            }}
          >
           <div
  style={{
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
    flexWrap: "wrap",
    marginBottom: 10,
  }}
>
  <div style={{ fontSize: 13, opacity: 0.75 }}>
    Choose a Shot for each line (that Shot's voice will be used). This builds the script automatically.
  </div>

  <label
    style={{
      display: "flex",
      alignItems: "center",
      gap: 10,
      fontSize: 13,
      opacity: 0.95,
      fontWeight: 900,
    }}
  >
    <input
      type="checkbox"
      checked={useDialogueMode}
      onChange={(e) => setUseDialogueMode(e.target.checked)}
    />
    Dialogue mode
  </label>
</div>

            <div style={{ display: "grid", gap: 10 }}>
              {scriptLines.map((l, idx) => (
                <div
                  key={idx}
                  style={{
                    display: "flex",
                    gap: 10,
                    alignItems: "flex-start",
                    flexWrap: "wrap",
                    padding: 10,
                    borderRadius: 12,
                    border: "1px solid rgba(255,255,255,0.10)",
                    background: "rgba(0,0,0,0.10)",
                  }}
                >
                  <div style={{ fontWeight: 900, opacity: 0.85, width: 64, paddingTop: 8 }}>Line {idx + 1}</div>

                  <select
                    value={l.shot_tag}
                    onChange={(e) => updateScriptLine(idx, { shot_tag: e.target.value })}
                    style={{
                      width: 110,
                      padding: 8,
                      borderRadius: 10,
                      border: "1px solid rgba(255,255,255,0.14)",
                      background: "rgba(255,255,255,0.04)",
                      color: "#eaeaea",
                    }}
                  >
                    {shots.map((s) => (
                      <option key={s.tag} value={s.tag}>
                        Shot {s.tag}
                      </option>
                    ))}
                  </select>

                  <div
                    style={{
                      width: 44,
                      height: 44,
                      borderRadius: 10,
                      overflow: "hidden",
                      border: "1px solid rgba(255,255,255,0.14)",
                      background: "rgba(255,255,255,0.04)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      marginTop: 1,
                    }}
                    title={l.shot_tag ? `Shot ${l.shot_tag}` : "No shot selected"}
                  >
                    {shotThumbUrls?.[l.shot_tag] ? (
                      <img
                        src={shotThumbUrls[l.shot_tag]}
                        alt={`Shot ${l.shot_tag}`}
                        style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
                      />
                    ) : (
                      <div style={{ fontSize: 11, opacity: 0.55, fontWeight: 900 }}>No shot</div>
                    )}
                  </div>

                  <textarea
                    rows={2}
                    value={l.text}
                    onChange={(e) => updateScriptLine(idx, { text: e.target.value })}
                    placeholder="Write the line here..."
                    style={{
                      flex: 1,
                      minWidth: 320,
                      padding: 10,
                      borderRadius: 12,
                      border: "1px solid rgba(255,255,255,0.14)",
                      background: "rgba(255,255,255,0.03)",
                      color: "#eaeaea",
                      fontSize: 14,
                      lineHeight: 1.35,
                      resize: "vertical",
                    }}
                  />

                  {/* Emotion preset dropdown */}
                  <select
                    defaultValue=""
                    onChange={(e) => {
                      const v = e.target.value;
                      if (!v) return;
                      applyEmotionTag(idx, v);
                      // reset to placeholder after applying
                      e.target.value = "";
                    }}
                    style={{
                      width: 170,
                      padding: 8,
                      borderRadius: 10,
                      border: "1px solid rgba(255,255,255,0.14)",
                      background: "rgba(255,255,255,0.04)",
                      color: "#eaeaea",
                      fontWeight: 900,
                      marginTop: 2,
                    }}
                    title="Insert an emotion tag at the start of this line"
                  >
                    <option value="">Emotion</option>
                    <option value="angry">Angry</option>
                    <option value="happy">Happy</option>
                    <option value="sad">Sad</option>
                    <option value="whispers">Whispers</option>
                    <option value="shouts">Shouts</option>
                    <option value="laughs">Laughs</option>
                    <option value="sighs">Sighs</option>
                    <option value="nervous">Nervous</option>
                    <option value="calm">Calm</option>
                  </select>

                  <button
                    onClick={() => removeScriptLine(idx)}
                    disabled={scriptLines.length <= 1}
                    style={{
                      padding: "8px 10px",
                      borderRadius: 12,
                      border: "1px solid rgba(255,255,255,0.14)",
                      background: "rgba(255,255,255,0.03)",
                      color: "#eaeaea",
                      cursor: scriptLines.length <= 1 ? "not-allowed" : "pointer",
                      opacity: scriptLines.length <= 1 ? 0.4 : 0.9,
                      fontWeight: 900,
                      marginTop: 2,
                    }}
                  >
                    Remove
                  </button>
                </div>
              ))}
            </div>

            <div style={{ marginTop: 12, display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
              <button
                onClick={addScriptLine}
                style={{
                  padding: "10px 14px",
                  borderRadius: 12,
                  border: "1px solid rgba(255,255,255,0.14)",
                  background: "rgba(255,255,255,0.06)",
                  color: "#eaeaea",
                  cursor: "pointer",
                  fontWeight: 900,
                }}
              >
                Add line
              </button>

              <button
                onClick={() => setShowScriptImport((v) => !v)}
                style={{
                  padding: "10px 14px",
                  borderRadius: 12,
                  border: "1px solid rgba(255,255,255,0.14)",
                  background: "rgba(255,255,255,0.03)",
                  color: "#eaeaea",
                  cursor: "pointer",
                  fontWeight: 900,
                  opacity: 0.92,
                }}
              >
                {showScriptImport ? "Hide" : "Import"}
              </button>

              <div style={{ fontSize: 12, opacity: 0.65 }}>
                Tip: You can use tags like [angry] or [whispers] inside a line for performance.
              </div>
            </div>

            {showScriptImport && (
              <div style={{ marginTop: 12 }}>
                <div style={{ fontWeight: 800, marginBottom: 6 }}>Paste script to import (format: `1: hello`)</div>
                <textarea
                  rows={6}
                  value={script}
                  onChange={(e) => setScript(e.target.value)}
                  placeholder={`1: Hi Ted. How are you?\n2: I'm good Tom. How are you?\n3: I'm also good. Thanks for asking.\n1: Of course.`}
                  style={{
                    width: "100%",
                    padding: 12,
                    borderRadius: 12,
                    border: "1px solid rgba(255,255,255,0.14)",
                    background: "rgba(255,255,255,0.03)",
                    color: "#eaeaea",
                    fontSize: 14,
                    lineHeight: 1.35,
                  }}
                />
                <div style={{ marginTop: 10, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                  <button
                    onClick={() => setScriptLines(parseScriptToLines(script))}
                    style={{
                      padding: "10px 14px",
                      borderRadius: 12,
                      border: "1px solid rgba(255,255,255,0.14)",
                      background: "rgba(255,255,255,0.06)",
                      color: "#eaeaea",
                      cursor: "pointer",
                      fontWeight: 900,
                    }}
                  >
                    Parse into lines
                  </button>
                  <div style={{ fontSize: 12, opacity: 0.6 }}>
                    This will overwrite the current line list.
                  </div>
                </div>
              </div>
            )}

            {missingVisualTags.length === 0 && scriptVisualTags.length > 0 && (
              <div style={{ marginTop: 10, fontSize: 12, opacity: 0.6 }}>
                Visual tags detected: {scriptVisualTags.join(", ")}
              </div>
            )}
          </div>
      ) : (
        <div
          style={{
            marginTop: 10,
            padding: 14,
            borderRadius: 12,
            border: "1px solid rgba(255,255,255,0.14)",
            background: "rgba(255,255,255,0.04)",
          }}
        >
          <div style={{ fontSize: 13, opacity: 0.75, marginBottom: 10 }}>
            Upload one audio clip per line. Assign each line to a Shot number. Voice is taken from that Shot.
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10, flexWrap: "wrap" }}>
            <label style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 13, opacity: 0.95 }}>
              <input
                type="checkbox"
                checked={useNativeAudio}
                onChange={(e) => setUseNativeAudio(e.target.checked)}
              />
              Use native audio (no voice conversion)
            </label>
            <div style={{ fontSize: 12, opacity: 0.65 }}>
              {useNativeAudio
                ? "Voices are ignored. We only normalize your upload to WAV."
                : "Voices are used to convert audio to the selected character voices."}
            </div>
          </div>

          <div style={{ display: "grid", gap: 10 }}>
            {audioLines.map((l, idx) => (
              <div
                key={idx}
                style={{
                  display: "flex",
                  gap: 10,
                  alignItems: "center",
                  flexWrap: "wrap",
                  padding: 10,
                  borderRadius: 12,
                  border: "1px solid rgba(255,255,255,0.10)",
                  background: "rgba(0,0,0,0.10)",
                }}
              >
                <div style={{ fontWeight: 900, opacity: 0.85, width: 64 }}>Line {idx + 1}</div>

                <select
                  value={l.shot_tag}
                  onChange={(e) => updateAudioLine(idx, { shot_tag: e.target.value })}
                  style={{
                    width: 110,
                    padding: 8,
                    borderRadius: 10,
                    border: "1px solid rgba(255,255,255,0.14)",
                    background: "rgba(255,255,255,0.04)",
                    color: "#eaeaea",
                  }}
                >
                  {shots.map((s) => (
                    <option key={s.tag} value={s.tag}>
                      Shot {s.tag}
                    </option>
                  ))}
                </select>

                <div
                  style={{
                    width: 44,
                    height: 44,
                    borderRadius: 10,
                    overflow: "hidden",
                    border: "1px solid rgba(255,255,255,0.14)",
                    background: "rgba(255,255,255,0.04)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                  title={l.shot_tag ? `Shot ${l.shot_tag}` : "No shot selected"}
                >
                  {shotThumbUrls?.[l.shot_tag] ? (
                    <img
                      src={shotThumbUrls[l.shot_tag]}
                      alt={`Shot ${l.shot_tag}`}
                      style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
                    />
                  ) : (
                    <div style={{ fontSize: 11, opacity: 0.55, fontWeight: 900 }}>No shot</div>
                  )}
                </div>

                <button
                  onClick={() => toggleAudioPreview(idx)}
                  disabled={!audioPreviewUrls?.[idx]}
                  style={{
                    padding: "8px 10px",
                    borderRadius: 12,
                    border: "1px solid rgba(255,255,255,0.14)",
                    background: "rgba(255,255,255,0.03)",
                    color: "#eaeaea",
                    cursor: audioPreviewUrls?.[idx] ? "pointer" : "not-allowed",
                    opacity: audioPreviewUrls?.[idx] ? 0.9 : 0.4,
                    fontWeight: 900,
                    minWidth: 92,
                  }}
                  title={audioPreviewUrls?.[idx] ? "Preview this line's audio" : "Upload audio to enable preview"}
                >
                  {playingAudioIdx === idx ? "Stop" : "Preview"}
                </button>

                <label
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 10,
                    padding: "8px 12px",
                    borderRadius: 12,
                    border: "1px solid rgba(255,255,255,0.14)",
                    background: "rgba(255,255,255,0.06)",
                    cursor: "pointer",
                    fontWeight: 900,
                  }}
                >
                  <input
                    type="file"
                    accept="audio/*"
                    style={{ display: "none" }}
                    onChange={(e) => updateAudioLine(idx, { file: e.target.files?.[0] || null })}
                  />
                  {l.file ? "Replace audio" : "Upload audio"}
                </label>

                <div style={{ fontSize: 13, opacity: 0.8, minWidth: 220 }}>
                  {l.file ? l.file.name : "No audio selected"}
                </div>

                <button
                  onClick={() => removeAudioLine(idx)}
                  disabled={audioLines.length <= 1}
                  style={{
                    padding: "8px 10px",
                    borderRadius: 12,
                    border: "1px solid rgba(255,255,255,0.14)",
                    background: "rgba(255,255,255,0.03)",
                    color: "#eaeaea",
                    cursor: audioLines.length <= 1 ? "not-allowed" : "pointer",
                    opacity: audioLines.length <= 1 ? 0.4 : 0.9,
                    fontWeight: 900,
                  }}
                >
                  Remove
                </button>
              </div>
            ))}
          </div>

          <div style={{ marginTop: 12, display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
            <button
              onClick={addAudioLine}
              style={{
                padding: "10px 14px",
                borderRadius: 12,
                border: "1px solid rgba(255,255,255,0.14)",
                background: "rgba(255,255,255,0.06)",
                color: "#eaeaea",
                cursor: "pointer",
                fontWeight: 900,
              }}
            >
              Add line
            </button>
            <div style={{ fontSize: 12, opacity: 0.65 }}>
              Tip: keep each line a clean single take. Trim dead air for tighter sync.
            </div>
          </div>
        </div>
      )}

      <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 14, flexWrap: "wrap" }}>
        <button
          disabled={!canGenerate}
          onClick={generate}
          style={{
            padding: "12px 18px",
            borderRadius: 12,
            border: "1px solid rgba(255,255,255,0.14)",
            background: busy ? "rgba(255,255,255,0.03)" : "rgba(255,255,255,0.08)",
            color: "#eaeaea",
            cursor: canGenerate ? "pointer" : "not-allowed",
            fontSize: 16,
            fontWeight: 900,
            letterSpacing: 0.4,
          }}
        >
          {busy ? "Working..." : inputMode === "script" ? "Generate Video" : "Convert + Generate"}
        </button>

        <div style={{ opacity: 0.85, fontSize: 14 }}>{status}</div>
      </div>

      {(previewUrl || outputs.length > 0) && (
        <div style={{ marginTop: 18 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <div style={{ fontWeight: 900, fontSize: 18 }}>Preview</div>
            {previewUrl && (
              <button
                onClick={() => setShowPreview((v) => !v)}
                style={{
                  padding: "10px 14px",
                  borderRadius: 12,
                  border: "1px solid rgba(255,255,255,0.14)",
                  background: "rgba(255,255,255,0.06)",
                  color: "#eaeaea",
                  cursor: "pointer",
                  fontWeight: 900,
                }}
              >
                {showPreview ? "Hide" : "Play"}
              </button>
            )}
          </div>

          {showPreview && previewUrl && (
            <div style={{ marginTop: 12 }}>
              <video src={previewUrl} controls style={{ width: "100%", maxWidth: 980, borderRadius: 12 }} />
            </div>
          )}

          {outputs.length > 0 && (
            <div style={{ marginTop: 14 }}>
              <div style={{ fontWeight: 900, marginBottom: 8 }}>Downloads</div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 6 }}>
                {previewUrl && (
                  <a href={previewUrl} target="_blank" rel="noreferrer" style={{ color: "#9ad1ff", opacity: 0.95 }}>
                    preview.mp4
                  </a>
                )}
                {outputs.map((o) => (
                  <a key={o.name} href={o.url} target="_blank" rel="noreferrer" style={{ color: "#9ad1ff", opacity: 0.95 }}>
                    {o.name}
                  </a>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      </div>
    </div>
  );
}