# ComfyUI-AudioQC《配音出厂质检闸》

给 TTS/配音成品上出厂闸的原创 ComfyUI 节点。逐句机器耳量测，拦三类出厂硬伤，判决+JSON 报告落盘：

- **杂声尺（硬 FAIL）**：no_speech_prob > ns_max（默认 0.04）——"额外声音/棕噪声"类病。
- **吞字尺（硬 FAIL）**：转写汉字数 vs 台词差 > 2——漏字/串台。
- **忽快忽慢尺（警告）**：逐句 1.0s 整宽窗句内语速比 > max_ratio（默认 2.8）——同栈实测方差大，只警告不硬拦，复测+人耳裁。
- **太平提示（警告）**：句间语速台阶 p50 < 5%——真人自然摆动在 19-31%，太平=AI 味来源之一。

输出 `(verdict, report_json)`；报告另落 `out_path`。可选 `audio_file` 直读原文件（与标定同口径），不走张量往返。

## 安装

```
cd ComfyUI/custom_nodes
git clone https://github.com/REPLACE_OWNER/ComfyUI-AudioQC.git
pip install -r ComfyUI-AudioQC/requirements.txt
```

ASR 权重：默认找本地 `AUDIOQC_WHISPER_DIR` 指向的 faster-whisper-small 目录；未设置时按 HF 仓库名 `Systran/faster-whisper-small` 自动下载（CPU int8 推理，不占显存）。

## ⚠️ 口径绑栈（换环境必读）

ns / 语速比读数**随 ASR 库版本栈系统性漂移**（跨栈对表实锤：py3.12+ctranslate2 4.6 vs py3.13+4.8，同一文件 ns 0.0441↔0.0285、正例句 ratio 1.4↔2.67）。**默认阈值只对本栈标定成立；换 Python/ctranslate2/faster-whisper 任一版本，必须重标定后才可当硬闸用。**

重标方法：用 `comfyui_audioqc/calib_stack.py` 在你的栈上跑一批已知正例/负例，取正例上沿+负例下沿之间定 `ns_max`/`max_ratio`，再改节点默认值。守门与标定必须同栈。

## 已知局限（如实登记）

- 句内拖慢型"太平病"ns 尺抓不住（属警告族，交人耳终裁）；
- 22050Hz 重采样对读数有轻微影响；
- 语气字（"嗯"）转写不稳；
- 1s 窗速度比在短句上是刀口尺，无硬闸资格（同栈跨跑仍抖）。

**本闸只拦出厂硬伤，感官终裁永远在人耳。**

## License

MIT
