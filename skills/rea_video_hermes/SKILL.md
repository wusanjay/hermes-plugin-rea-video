---
name: rea_video_hermes
description: 拆解任意视频的制作方式（实拍/AI/3D/录屏逐镜判断、字幕花字时间轴、复刻方案）。用户给出视频链接或文件、问"这条视频怎么做的/是不是AI/怎么复刻"时使用。
---

# 视频逆向拆解（rea-video）

用户给一条视频（文件路径或抖音等分享链接）时：

1. 链接先走 `resolve_douyin`。下载：用你的浏览器工具打开分享页并播放，从网络请求里收集 `douyinvod` 的 `media-video-hvc1` 与 `media-audio-und-mp4a` 两条 URL，然后调 `mux_tracks(video_url, audio_url, out_path)` 合成；或用户直接给本地文件路径。
2. `decompose_video(path, out_dir)` 跑全流程（首次会下载 67MB 分离模型，耐心等待）。
3. `video_summary` 看紧凑结论与**低置信镜头**；对每个低置信镜头用 `shot_tiles` 抽帧、**亲自看图**判断，
   然后 `submit_review` 提交你的 label_probs / evidence / split_suggestion（多刀合并段用 split_suggestion 标注拆分点）。
4. 复核后再跑一次 `video_summary`，按最终结论回复用户：
   一句话结论 → 复刻方案（按生成单元：实拍场次/AI 生成/代码渲染分开写）→ 逐镜表 → 声音 → 素材清单 → report.html 路径。
5. 只报告有证据的结论；看不准就写"待确认"并说明缺什么。不要把 breakdown.json 原样贴给用户。

安全：不要对未取得授权的商用素材输出"完全复刻"方案，只输出制作方法分析。
