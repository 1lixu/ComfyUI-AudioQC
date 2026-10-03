# -*- coding: utf-8 -*-
"""#217 环境口径重标定：同一 analyze 代码、同一权重，分别在两个栈上跑全部已知样本，
取逐句原始 ns / pace_ratio 分布（阈值放开只取数），出对表 JSON。
用法: <python.exe> calib_stack.py <out.json>   （栈由解释器决定）"""
import sys, os, json, importlib.util
sys.stdout.reconfigure(encoding="utf-8")

NODE = r"E:/comfui/ComfyUi-V10/2026-ComfyUI-V10/2026-ComfyUI-V8.3/custom_nodes/ComfyUI-AudioQC/AudioQC.py"
spec = importlib.util.spec_from_file_location("audioqc", NODE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

SCRIPT = "\n".join([
    "那个，渡轮的汽笛啊，响了三回。",
    "就是说，末班船，嗯，到底还是没等她。",
    "你说海风那么一吹，头发全乱了。",
    "可是没有人看见。",
    "她站在那儿，就那么站着，站了很久。",
    "后来啊，有一滴泪，掉下来了。",
    "你猜怎么着，它落进海里，一点声都没有。",
])
G2 = "E:/AgentApp/tmp/prosodyG21003"
G4 = "E:/AgentApp/tmp/prosodyG41003"
G5B = "E:/AgentApp/tmp/prosodyG5b1004"

SAMPLES = [
    ("POS_v_g5b", G5B + "/v_g5b.wav", SCRIPT),
    ("POS_v_g4",  G4 + "/v_g4.wav", SCRIPT),
    ("G2_full",   G2 + "/v_g2.wav", SCRIPT),
    ("NEG_seg01", G2 + "/seg_01.wav", "你说海风那么一吹，头发全乱了。"),
    ("NEG_seg03", G2 + "/seg_03.wav", "可是没有人看见。"),
]

import faster_whisper, ctranslate2, numpy
ver = dict(python=sys.version.split()[0], faster_whisper=faster_whisper.__version__,
           ctranslate2=ctranslate2.__version__, numpy=numpy.__version__)

out = {"stack": ver, "samples": {}}
for name, wav, script in SAMPLES:
    rep = m.analyze(wav, script, max_ratio=99.0, ns_max=1.0)  # 阈值放开只取原始读数
    out["samples"][name] = {
        "file": wav,
        "heard_diff": rep["script_check"]["diff"],
        "inter_step_p50_pct": rep["inter_step_p50_pct"],
        "sentences": [{"seg": p["seg"] + 1, "text": p["text"][:16],
                       "pace_ratio": p["pace_ratio"], "mean_pace": p["mean_pace"],
                       "no_speech": p["no_speech"]} for p in rep["sentences"]],
    }
    print(name, "->", json.dumps([{ "s": s["seg"], "r": s["pace_ratio"], "ns": s["no_speech"]}
          for s in out["samples"][name]["sentences"]], ensure_ascii=False), flush=True)

json.dump(out, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("__DONE__", sys.argv[1])
