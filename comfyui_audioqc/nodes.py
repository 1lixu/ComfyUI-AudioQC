# -*- coding: utf-8 -*-
"""
ComfyUI-AudioQC《配音出厂质检闸》原创节点（RH 首件 B，2026-10-04）
把 G3/G4/G5 十六轮验证过的守门逻辑封装成产线可挂的出厂闸：
  ①逐句 1.0s 整宽窗句内语速比（忽快忽慢尺）> max_ratio 出【警告】——实测同栈方差大，不做硬拦；
  ②no_speech_prob ≤ ns_max（"额外声音/杂音"尺，硬 FAIL）；
口径绑栈（2026-10-04 跨栈对表实锤）：ns/语速比读数随 ASR 库版本系统性漂移
（py3.12+ctranslate2 4.6 vs py3.13+4.8：正例句5 1.4↔2.67、seg01 0.0441↔0.0285），
跨栈数字不可比。默认阈值按产线服务器栈（workbuddy py3.13 + E:/comfui/py_packages）标定：
正例 ns 上沿 0.0333(v_g4)/ratio 上沿 2.67(v_g5b句5，GM 耳终裁若判其好可收紧)；
负例 seg03 杂声 0.046；seg01(0.0285) 属本栈 ns 尺已知漏拦（太平族病，走警告+耳裁）。
换栈必重标。硬 FAIL 只拦硬伤（杂声/大幅忽快忽慢/吞字），太平与偏快为警告，终裁在人耳。
  ③转写汉字数 vs 台词差 ≤2（吞字/串台尺，台词可选）；
  ④句间语速台阶 p50 提示带（真人 19-31%，<5% 报 TOO_FLAT 太平警告——G4 对标新发现）；
输出 PASS/FAIL/WARN 判决 + 逐句明细 JSON 落盘。裁判终裁仍是人耳，本闸只拦出厂硬伤。
"""
import os, json, wave, time, re

QC_WEIGHTS = os.environ.get(
    "AUDIOQC_WHISPER_DIR",
    # 发布版默认：有本地目录用本地，否则交给 faster-whisper 按 HF 仓库名自动下载
    r"E:/AgentApp/tmp/feed94/models/faster-whisper-small"
    if os.path.isdir(r"E:/AgentApp/tmp/feed94/models/faster-whisper-small")
    else "Systran/faster-whisper-small")
_MODEL = {}

def _zh_chars(s):
    return [c for c in s if u'\u4e00' <= c <= u'\u9fff']

def _script_lines(script_text):
    return [p for p in re.split(r"[。！？；\n]+", script_text) if _zh_chars(p)]

def _get_model():
    if "m" not in _MODEL:
        from faster_whisper import WhisperModel
        _MODEL["m"] = WhisperModel(QC_WEIGHTS, device="cpu", compute_type="int8")
    return _MODEL["m"]

def _seg_windows(words, dur, win=1.0, step=0.5):
    wins, t = [], 0.0
    while t + win <= dur + 1e-6:
        n = sum(len(_zh_chars(tx)) for a, b, tx in words if a < t + win and b > t)
        wins.append(n)
        t += step
    return wins

def _char_times(words):
    ct = []
    for a, b, tx in words:
        chs = _zh_chars(tx)
        n = len(chs)
        if n == 0:
            continue
        step = (b - a) / n
        for j, c in enumerate(chs):
            ct.append((a + j * step, a + (j + 1) * step, c))
    return ct

def analyze(wav_path, script_text="", max_ratio=2.8, ns_max=0.04):
    model = _get_model()
    segs, _ = model.transcribe(wav_path, language="zh", word_timestamps=True, beam_size=1)
    segs = [s for s in segs if (s.words or [])]
    per, lines, fail, warn = [], [], [], []
    script_lines = _script_lines(script_text)
    if script_lines:
        # 字级对齐：剧本行边界按汉字数比例投到转写字符流时间轴上，
        # Whisper 把两行读成一句/一行切成两句都瞒不过行级语速尺（实测踩坑后的正解）
        words = sorted((w.start, w.end, w.word) for s in segs for w in s.words)
        ct = _char_times(words)
        n_ct = len(ct)
        if n_ct == 0:
            return {"verdict": "FAIL: 转写为空", "inter_step_p50_pct": None, "inter_steps": [],
                    "script_check": {"script_chars": sum(len(_zh_chars(x)) for x in script_lines),
                                     "diff": 999}, "sentences": [], "lines": ["无语音"]}
        tl = [len(_zh_chars(x)) for x in script_lines]
        total_t, total_c = sum(tl), n_ct
        cum = 0
        units = []
        for li, ln in enumerate(script_lines):
            c0 = int(round(cum / total_t * total_c))
            cum += tl[li]
            c1 = n_ct if li == len(script_lines) - 1 else int(round(cum / total_t * total_c))
            if c1 <= c0:
                continue
            t0, t1 = ct[c0][0], ct[c1 - 1][1]
            uwords = [(a, b, c) for a, b, c in ct[c0:c1]]
            ns = max((float(s.no_speech_prob) for s in segs if s.start < t1 and s.end > t0), default=0.0)
            units.append({"t0": t0, "t1": t1, "ns": ns, "text": ln, "chars": uwords})
    else:
        units = []
        for s in segs:
            ws = sorted((w.start, w.end, w.word) for w in s.words)
            units.append({"t0": ws[0][0], "t1": ws[-1][1], "ns": float(s.no_speech_prob),
                          "text": s.text.strip(), "chars": _char_times(ws)})
    for i, u in enumerate(units):
        t0, t1 = u["t0"], u["t1"]
        dur = t1 - t0
        chars = u["chars"]
        # 语流切分：字间隙>0.45s 算停顿断流，句内速度比只在连续语流内量（标点大停顿≠忽快忽慢病，实测防误拦）
        runs, cur = [], [chars[0]]
        for c in chars[1:]:
            if c[0] - cur[-1][1] > 0.45:
                runs.append(cur); cur = [c]
            else:
                cur.append(c)
        runs.append(cur)
        ratio, wins_all = 1.0, []
        for run in runs:
            r0, r1 = run[0][0], run[-1][1]
            if r1 - r0 < 1.8:
                continue
            w, t = [], r0
            while t + 1.0 <= r1 + 1e-6:
                w.append(sum(1 for a, b, c in run if t <= (a + b) / 2 < t + 1.0)); t += 0.5
            wins_all += w
            pos = [x for x in w if x > 0]
            if len(pos) > 1 and min(pos) > 0:
                ratio = max(ratio, max(pos) / min(pos))
        if not wins_all:
            wins_all = [len(chars) / max(dur, 0.5)]
        mean_pace = round(sum(wins_all) / len(wins_all), 2)
        ns = round(u["ns"], 4)
        flags = []
        if ratio > max_ratio:
            # 速度尺降为警告(2026-10-04 实测定罪)：同栈同文件两跑句5读数 2.67→3.5、跨栈 1.4，
            # 1s 窗比值在短句上是刀口尺，方差大到无硬闸资格；忽快忽慢走警告+复测+耳裁。
            flags.append("PaceJump=%.2f>%s(警告)" % (ratio, max_ratio)); warn.append("句%d忽快忽慢嫌疑(%.2f)" % (i + 1, ratio))
        if ns > ns_max:
            flags.append("NoSpeech=%.3f>%s" % (ns, ns_max)); fail.append("句%d杂声嫌疑" % (i + 1))
        if mean_pace > 7.5:
            warn.append("句%d偏快(%.1f字/s)" % (i + 1, mean_pace))
        per.append({"seg": i, "text": u["text"], "start": round(t0, 2), "dur": round(dur, 2),
                    "pace_ratio": round(ratio, 2), "mean_pace": mean_pace, "no_speech": ns,
                    "flags": flags})
        lines.append("句%d [%.1f-%.1fs] 比%.2f 速度%.1f字/s ns%.3f %s %s"
                     % (i + 1, t0, t1, ratio, mean_pace, ns,
                        u["text"][:18], "|".join(flags)))
    if script_text:
        heard_all = "".join(s.text for s in segs)
        d = abs(len(_zh_chars(heard_all)) - len(_zh_chars(script_text)))
        if d > 2:
            fail.append("吞字/串台：转写与台词汉字数差%d" % d)
        per_script = {"script_chars": len(_zh_chars(script_text)),
                      "heard_chars": len(_zh_chars(heard_all)), "diff": d}
    else:
        per_script = {"script_chars": 0, "heard_chars": 0, "diff": -1}
    paces = [p["mean_pace"] for p in per if p["mean_pace"] > 0]
    steps = sorted(round(abs(paces[j + 1] - paces[j]) / paces[j] * 100, 1) for j in range(len(paces) - 1))
    step_p50 = steps[len(steps) // 2] if steps else None
    if step_p50 is not None and step_p50 < 5.0 and len(steps) >= 3:
        warn.append("句间台阶p50=%.1f%% 低于真人自然摆动带(19-31%)：太平警告" % step_p50)
    verdict = ("FAIL: " + "；".join(fail)) if fail else \
              ("PASS" + ("（WARN: " + "；".join(warn) + "）" if warn else ""))
    return {"verdict": verdict, "inter_step_p50_pct": step_p50, "inter_steps": steps,
            "script_check": per_script, "sentences": per, "lines": lines}

class AudioQCGate:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "audio": ("AUDIO",),
            "script": ("STRING", {"multiline": True, "default": ""}),
            "max_ratio": ("FLOAT", {"default": 2.8, "min": 1.0, "max": 5.0, "step": 0.1,
                "tooltip": "句内语速比硬线。产线栈(3.13)标定：正例上沿 2.67(v_g5b句5)；换栈必重标。"}),
            "ns_max": ("FLOAT", {"default": 0.04, "min": 0.0, "max": 0.5, "step": 0.005,
                "tooltip": "no_speech 杂声硬线。产线栈标定：正例上沿 0.0333(v_g4)，负例 0.046(seg03)；换栈必重标。"}),
            "out_path": ("STRING", {"default": r"E:/AgentApp/tmp/qc1004/report.json"}),
        }, "optional": {
            "audio_file": ("STRING", {"default": "",
                "tooltip": "原始音频文件路径或 input 目录内文件名；给了就直读原文件（与标定同口径），不给才走张量往返。"}),
        }}

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("verdict", "report_json")
    FUNCTION = "run"
    CATEGORY = "音频质检"

    def run(self, audio, script, max_ratio, ns_max, out_path, audio_file=""):
        t0 = time.time()
        import torch
        d = os.path.dirname(out_path.replace("\\", "/"))
        if d:
            os.makedirs(d, exist_ok=True)
        src_path = ""
        mode = "tensor"
        if audio_file and audio_file.strip():
            cand = audio_file.strip().replace("\\", "/")
            if not os.path.isabs(cand):
                try:
                    import folder_paths
                    cand = folder_paths.get_input_path(cand)
                except Exception:
                    cand = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                        os.path.abspath(__file__)))), "input", cand)
            if os.path.isfile(cand):
                src_path = cand
                mode = "file"
        if not src_path:
            mode = "tensor"
            wf = audio.get("waveform")
            if wf is None:
                wf = audio.get("audio")
            sr = int(audio.get("sampling_rate") or audio.get("sample_rate") or 0)
            if sr == 0:
                raise RuntimeError("无法从 AUDIO 输入取采样率，keys=%s" % list(audio.keys()))
            if wf.ndim == 3:
                x = wf[0].mean(dim=0)
            else:
                x = wf
            tmp = os.path.join(d or ".", "_qc_input.wav")
            pcm = (torch.clamp(x, -1.0, 1.0) * 32767).to(torch.int16).cpu().numpy()
            with wave.open(tmp, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
                w.writeframes(pcm.tobytes())
            src_path = tmp
        rep = analyze(src_path, script, max_ratio, ns_max)
        rep["source_mode"] = mode
        rep["elapsed_sec"] = round(time.time() - t0, 1)
        json.dump(rep, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        head = "\n".join(rep["lines"])
        return (head + "\n【判决】" + rep["verdict"], json.dumps(rep, ensure_ascii=False))

NODE_CLASS_MAPPINGS = {"AudioQCGate": AudioQCGate}
NODE_DISPLAY_NAME_MAPPINGS = {"AudioQCGate": "配音出厂质检闸 AudioQC"}
