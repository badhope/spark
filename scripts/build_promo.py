import pathlib
import subprocess

shots = [
    ("10-empty-state.png", "Spark · 浏览器原生的 AI 编程 Agent"),
    ("11-chat.png", "真实模型 · 工具调用 · 流式回答"),
    ("20-inline-shell.png", "!cmd 内联 shell · 结果不进模型上下文"),
    ("12-settings-model.png", "任意 OpenAI 兼容端点 · 多模型档案"),
    ("13-settings-permissions.png", "六档审批 × 四档沙箱"),
    ("21-settings-display.png", "桌面通知 · 主题 · 中英双语"),
    ("16-file-viewer.png", "文件查看器 · 语法高亮"),
    ("15-command-palette.png", "命令面板 · 单文件 HTML 分享"),
    ("24-mobile-shell.png", "移动端同样完整"),
]
font = "C\\:/Windows/Fonts/msyh.ttc"
out = pathlib.Path("assets/video_frames")
out.mkdir(exist_ok=True)
for i, (name, cap) in enumerate(shots):
    src = pathlib.Path("assets/screenshots") / name
    dst = out / f"frame{i:02d}.png"
    # 先缩放到 1600x960（留 140px 字幕带），再在底部黑带上写标题：
    # drawtext 的 y 是相对 pad 后画布，早先 y=h-78 会落到画布外。
    vf = (
        "scale=1600:960:force_original_aspect_ratio=decrease,"
        "pad=1600:1100:(ow-iw)/2:0:color=0x14171c,"
        f"drawtext=fontfile='{font}':text='{cap}':fontcolor=white:fontsize=38:"
        "x=(w-text_w)/2:y=1000"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", vf, "-update", "1", str(dst)],
        check=True,
    )
    print("frame", dst.name)

# --- compose the tour -------------------------------------------------------
# Frames are built above; xfade chains them into assets/spark-demo.mp4 and the
# GIF preview. Kept in one script so the README's rebuild command actually
# reproduces the shipped video end to end.
import shutil

frames = sorted(out.glob("frame*.png"))
if not frames:
    raise SystemExit("no frames produced")
DUR = 3.0
FADE = 0.6

inputs: list[str] = []
for frame in frames:
    inputs += ["-loop", "1", "-t", str(DUR), "-i", str(frame)]

filters = [f"[{i}:v]scale=1600:1100,setsar=1,fps=30[v{i}]" for i in range(len(frames))]
last = "v0"
for i in range(1, len(frames)):
    nxt = f"x{i}"
    filters.append(
        f"[{last}][v{i}]xfade=transition=fade:duration={FADE}:"
        f"offset={DUR * i - FADE * i:.2f}[{nxt}]"
    )
    last = nxt
filters.append(f"[{last}]format=yuv420p[outv]")

subprocess.run(
    ["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filters),
     "-map", "[outv]", "-c:v", "libx264", "-preset", "slow", "-crf", "20",
     "-movflags", "+faststart", "assets/spark-demo.mp4"],
    check=True,
)
subprocess.run(
    ["ffmpeg", "-y", "-loglevel", "error", "-i", "assets/spark-demo.mp4",
     "-vf", "fps=1,scale=760:-1", "assets/share/spark-demo.gif"],
    check=True,
)
subprocess.run(
    ["ffmpeg", "-y", "-loglevel", "error", "-ss", "4", "-i", "assets/spark-demo.mp4",
     "-frames:v", "1", "-update", "1", "assets/share/preview-frame.png"],
    check=True,
)
# Intermediate frames are reproducible from the screenshots; do not leave them.
shutil.rmtree(out, ignore_errors=True)
print("wrote assets/spark-demo.mp4, assets/share/spark-demo.gif, assets/share/preview-frame.png")
