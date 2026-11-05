# Demo 清单（黑客松提交物的一半）

## 要录的
1. 90 秒屏幕录屏：Hermes 对话框粘贴抖音链接 → 全流程自动跑完 → 打开 report.html
   （候选目标视频已就位：大瑞Keaton BOT 片，报告 out/keaton/report.html 可作为"成品预览"先放）
2. 口播 30 秒开场：用你的口播流水线出片（脚本见下）

## 开场口播（30s）
"你刷到一条爆款视频，想知道它是怎么做的——实拍还是AI？哪个镜头能用AI复刻？
现在把链接丢给 Hermes 就行。它自己下载、自己逐镜判断实拍还是AI生成、自己提取字幕和音轨，
低置信的镜头它还会自己看图复核。最后给你一份带复刻方案的报告——这就是 rea-video 插件。"

## 要填的文件
- demo/sample_report/ 放 report.html + breakdown.json + subtitles.srt（从 out/keaton 拷贝）
- README 截图：report.html 首屏、storyboard.jpg、Hermes 对话截图 2 张
