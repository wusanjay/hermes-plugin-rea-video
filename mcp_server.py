"""Hermes Agent plugin: video reverse-engineering powered by JuneYaooo/rea-video.

Tools exposed to Hermes:
  resolve_douyin     share-link -> video id / canonical page
  decompose_video    run the full rea-video pipeline on a local video file
  video_summary      compact verdict + low-confidence shots for agent review
  shot_tiles         extract frames for a time range (agent looks at them itself)
  submit_review      write review answers and merge them (agent-as-VLM loop)
  mux_tracks         download Douyin CDN video+audio tracks and losslessly mux

Security model (per catalog review):
  * URLs: https only, hosts allowlisted (v.douyin.com / www.douyin.com /
    douyin.com for resolve; *.douyinvod.com for CDN tracks); curl runs with
    ``--proto =https`` and ``--`` so a value cannot be parsed as an option.
  * Outputs: every model-supplied output path is confined under REA_OUT_ROOT
    (set via mcp.json env, defaults to ~/rea-video-out); existing files are
    never overwritten; mux temp files are removed in a ``finally``.
  * Inputs to the rea_video CLI and ffmpeg must be absolute paths that exist.

Requires: ffmpeg, the pinned rea-video package, and the Hermes host's own
vision capability for the review loop (no external VLM key needed).
"""
from __future__ import annotations
import json, os, re, subprocess, sys
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from urllib.parse import urlparse

mcp = FastMCP("rea-video")
REA = [sys.executable, "-m", "rea_video"]  # or the installed `rea-video` entry point

_OUT_ROOT: Path | None = None


def _out_root() -> Path:
    """Resolve REA_OUT_ROOT lazily; fall back to ~/rea-video-out if unusable.

    Hermes expands ${PLUGIN_DATA} in mcp.json env values to the plugin's own
    data folder; a raw unexpanded value or an unwritable path falls back so the
    server still starts for anyone."""
    global _OUT_ROOT
    if _OUT_ROOT is not None:
        return _OUT_ROOT
    raw = os.environ.get("REA_OUT_ROOT", str(Path.home() / "rea-video-out"))
    try:
        p = Path(raw).expanduser()
        if "${" in str(p):
            raise ValueError("unexpanded template")
        p = p.resolve()
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        _OUT_ROOT = p
    except Exception:
        fb = Path.home() / "rea-video-out"
        fb.mkdir(parents=True, exist_ok=True)
        _OUT_ROOT = fb
    return _OUT_ROOT

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
RESOLVE_HOSTS = {"v.douyin.com", "www.douyin.com", "douyin.com"}
CDN_SUFFIX = ".douyinvod.com"


def _require_allowed_url(url: str, *, cdn: bool = False) -> None:
    """https-only + host allowlist; blocks file://, LAN, loopback, link-local."""
    u = urlparse(url)
    if u.scheme != "https":
        raise ValueError(f"只允许 https:// 链接，收到：{url[:80]}")
    host = (u.hostname or "").lower()
    ok = (host in RESOLVE_HOSTS) if not cdn else (host == CDN_SUFFIX.lstrip(".") or host.endswith(CDN_SUFFIX))
    if not ok:
        raise ValueError(f"主机不在白名单内：{host}")


def _safe_out(p: str, *, must_not_exist: bool = True) -> Path:
    """Confine a model-supplied output path under the output root; never overwrite."""
    root = _out_root()
    q = Path(p).expanduser()
    if not q.is_absolute():
        q = root / q
    q = q.resolve()
    if q != root and root not in q.parents:
        raise ValueError(f"输出路径必须在 {root} 之下，收到：{p}")
    if must_not_exist and q.exists():
        raise ValueError(f"目标已存在，拒绝覆盖：{q}")
    q.parent.mkdir(parents=True, exist_ok=True)
    return q


def _abs_input(p: str, what: str) -> str:
    q = Path(p).expanduser()
    if not q.is_absolute():
        raise ValueError(f"{what} 必须是绝对路径（不接受相对路径）：{p}")
    if not q.exists():
        raise ValueError(f"{what} 不存在：{p}")
    return str(q)


def _run(args: list[str], timeout: int = 1800) -> str:
    r = subprocess.run(REA + args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-800:] or f"rea-video exited {r.returncode}")
    return r.stdout


@mcp.tool()
def resolve_douyin(share_url: str) -> str:
    """Resolve a Douyin share link (https://v.douyin.com/... only) to its canonical video id and page."""
    _require_allowed_url(share_url)
    r = subprocess.run(["curl", "-s", "--proto", "=https", "-o", "/dev/null", "-w", "%{redirect_url}",
                        "-A", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
                        "--", share_url], capture_output=True, text=True, timeout=30)
    loc = r.stdout.strip()
    if loc:
        try:
            _require_allowed_url(loc, cdn=False)
        except ValueError:
            loc = ""
    vid = re.search(r"video/(\d+)", loc)
    return json.dumps({"canonical_page": loc, "video_id": vid.group(1) if vid else None,
                       "note": "下载需自行完成（工具或人工），decompose_video 接收本地文件路径"})


@mcp.tool()
def decompose_video(video_path: str, out_dir: str, ocr_lang: str = "zh") -> str:
    """Run the full rea-video pipeline: shot classification, captions/overlays, audio stems,
    storyboard, report.html + report.md. out_dir must be a fresh path under REA_OUT_ROOT."""
    vp = _abs_input(video_path, "video_path")
    od = _safe_out(out_dir, must_not_exist=True)
    od.mkdir(parents=True, exist_ok=True)
    out = _run(["decompose", vp, "-o", str(od), "--ocr-lang", ocr_lang])
    files = {f.name: str(f) for f in od.iterdir() if f.is_file()}
    return json.dumps({"log": out[-500:], "files": files,
                       "report_html": files.get("report.html"), "subtitles": files.get("packaging/subtitles.srt")}, ensure_ascii=False)


@mcp.tool()
def video_summary(breakdown_json: str) -> str:
    """Compact verdict: production-method percentages, low-confidence shots (review these), sound source.
    breakdown_json: a path to the breakdown file, or the breakdown JSON text itself."""
    s = breakdown_json.strip()
    if s[:1] in "{[":
        arg = str(_safe_out("summary-input.json", must_not_exist=False))
        Path(arg).write_text(s, encoding="utf-8")
    else:
        arg = _abs_input(s, "breakdown path")
    return _run(["summary", arg])


@mcp.tool()
def shot_tiles(video_path: str, start: float, end: float, every: float = 0.2, out_jpg: str = "") -> str:
    """Extract tiled frames for a time range so you (the agent) can look and judge a disputed shot."""
    vp = _abs_input(video_path, "video_path")
    out = _safe_out(out_jpg or "check.jpg")
    _run(["tiles", vp, "--start", str(start), "--end", str(end), "--every", str(every), "-o", str(out)])
    return json.dumps({"image": str(out)})


@mcp.tool()
def submit_review(out_dir: str, answers: dict, reviewer: str = "hermes") -> str:
    """Write review_answers.json (label_probs/evidence/split_suggestion per shot) and merge
    into the breakdown — the agent-as-VLM loop. Then call video_summary again."""
    od = Path(_abs_input(out_dir, "out_dir")).resolve()
    root = _out_root()
    if od != root and root not in od.parents:
        raise ValueError(f"out_dir 必须在 {root} 之下：{out_dir}")
    p = od / "review_answers.json"
    if p.exists():
        raise ValueError(f"review_answers.json 已存在，拒绝覆盖：{p}")
    p.write_text(json.dumps({"by": reviewer, **answers}, ensure_ascii=False, indent=1), encoding="utf-8")
    return _run(["apply-review", str(od), "--by", reviewer])


@mcp.tool()
def mux_tracks(video_url: str, audio_url: str, out_path: str) -> str:
    """Download separated video+audio CDN tracks (*.douyinvod.com only; UA and Referer
    set automatically) and losslessly mux them into out_path (under REA_OUT_ROOT, fresh)
    with ffmpeg. Pair with the browser extraction step: play the share page, collect
    douyinvod media-video / media-audio URLs."""
    _require_allowed_url(video_url, cdn=True)
    _require_allowed_url(audio_url, cdn=True)
    dst = _safe_out(out_path)
    v, a = dst.with_name(dst.name + ".v.mp4"), dst.with_name(dst.name + ".a.mp4")

    def _dl(url: str, out: Path) -> None:
        subprocess.run(["curl", "-s", "--noproxy", "*", "--proto", "=https", "-m", "300", "-A", UA,
                        "-H", "Referer: https://www.douyin.com/", "--", url, "-o", str(out)],
                       check=True, timeout=320)

    try:
        _dl(video_url, v)
        _dl(audio_url, a)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "mp4", "-i", str(v),
                        "-f", "mp4", "-i", str(a), "-c", "copy", "-movflags", "+faststart",
                        str(dst)], check=True, timeout=300)
    finally:
        v.unlink(missing_ok=True)
        a.unlink(missing_ok=True)
    return json.dumps({"saved": str(dst), "bytes": dst.stat().st_size})


if __name__ == "__main__":
    mcp.run()
