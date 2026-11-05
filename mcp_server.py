"""Hermes Agent plugin: video reverse-engineering powered by JuneYaooo/rea-video.

Tools exposed to Hermes:
  resolve_douyin     share-link -> video id / canonical page
  decompose_video    run the full rea-video pipeline on a local video file
  video_summary      compact verdict + low-confidence shots for agent review
  shot_tiles         extract frames for a time range (agent looks at them itself)
  submit_review      write review answers and merge them (agent-as-VLM loop)

Requires: ffmpeg, the pinned rea-video package, and the Hermes host's own
vision capability for the review loop (no external VLM key needed).
"""
from __future__ import annotations
import json, re, subprocess, sys
from pathlib import Path
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("rea-video")
REA = [sys.executable, "-m", "rea_video"]  # or the installed `rea-video` entry point

def _run(args: list[str], timeout: int = 1800) -> str:
    r = subprocess.run(REA + args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-800:] or f"rea-video exited {r.returncode}")
    return r.stdout

@mcp.tool()
def resolve_douyin(share_url: str) -> str:
    """Resolve a Douyin share link (v.douyin.com/...) to its canonical video id and page."""
    r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{redirect_url}",
                        "-A", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
                        share_url], capture_output=True, text=True, timeout=30)
    loc = r.stdout.strip()
    vid = re.search(r"video/(\d+)", loc)
    return json.dumps({"canonical_page": loc, "video_id": vid.group(1) if vid else None,
                       "note": "下载需自行完成（工具或人工），decompose_video 接收本地文件路径"})

@mcp.tool()
def decompose_video(video_path: str, out_dir: str, ocr_lang: str = "zh") -> str:
    """Run the full rea-video pipeline: shot classification, captions/overlays, audio stems,
    storyboard, report.html + report.md. Returns the generated file paths."""
    out = _run(["decompose", video_path, "-o", out_dir, "--ocr-lang", ocr_lang])
    files = {f.name: str(f) for f in Path(out_dir).iterdir() if f.is_file()}
    return json.dumps({"log": out[-500:], "files": files,
                       "report_html": files.get("report.html"), "subtitles": files.get("packaging/subtitles.srt")}, ensure_ascii=False)

@mcp.tool()
def video_summary(breakdown_json: str) -> str:
    """Compact verdict: production-method percentages, low-confidence shots (review these), sound source."""
    return _run(["summary", breakdown_json])

@mcp.tool()
def shot_tiles(video_path: str, start: float, end: float, every: float = 0.2, out_jpg: str = "") -> str:
    """Extract tiled frames for a time range so you (the agent) can look and judge a disputed shot."""
    out = out_jpg or "check.jpg"
    _run(["tiles", video_path, "--start", str(start), "--end", str(end), "--every", str(every), "-o", out])
    return json.dumps({"image": out})

@mcp.tool()
def submit_review(out_dir: str, answers: dict, reviewer: str = "hermes") -> str:
    """Write review_answers.json (label_probs/evidence/split_suggestion per shot) and merge
    into the breakdown — the agent-as-VLM loop. Then call video_summary again."""
    p = Path(out_dir) / "review_answers.json"
    p.write_text(json.dumps({"by": reviewer, **answers}, ensure_ascii=False, indent=1), encoding="utf-8")
    return _run(["apply-review", out_dir, "--by", reviewer])


@mcp.tool()
def mux_tracks(video_url: str, audio_url: str, out_path: str, referer: str = "https://www.douyin.com/") -> str:
    """Download separated video+audio CDN tracks (Douyin web-player style; UA and Referer
    required) and losslessly mux them into out_path with ffmpeg. Pair with the browser
    extraction step: play the share page, collect douyinvod media-video / media-audio URLs."""
    v, a = out_path + ".v.mp4", out_path + ".a.mp4"
    UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
    for url, dst in ((video_url, v), (audio_url, a)):
        subprocess.run(["curl", "-s", "--noproxy", "*", "-m", "300", "-A", UA,
                        "-H", f"Referer: {referer}", url, "-o", dst], check=True, timeout=320)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", v, "-i", a,
                    "-c", "copy", "-movflags", "+faststart", out_path], check=True, timeout=300)
    Path(v).unlink(missing_ok=True); Path(a).unlink(missing_ok=True)
    return json.dumps({"saved": out_path, "bytes": Path(out_path).stat().st_size})

if __name__ == "__main__":
    mcp.run()
