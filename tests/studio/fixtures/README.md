# 测试素材说明

本目录里的二进制素材是**本地生成**的小样本，用于验证媒体相关链路；不来自任何外部服务，也不包含第三方版权内容。

## sample-1s.mp4

- 用途：验证展示器的**视频宿主**（能否解码真实视频、播放结束后继续、跳过按钮、失败路径）。
- 规格：320×240、1 秒、15fps、H.264（baseline）+ MP4 容器。
- 大小：1948 字节。
- sha256：`944d54183d26f08051808be7fbbaa5556a32b93e90e5b7de90a8485f64991b16`
- 生成命令（本机 ffmpeg 8.1.1，离线执行）：

```powershell
ffmpeg -y -f lavfi -i "color=c=navy:s=320x240:d=1:r=15" -c:v libx264 -pix_fmt yuv420p -profile:v baseline -level 3.0 tests/studio/fixtures/sample-1s.mp4
```

- 校验命令：

```powershell
ffprobe -v error -show_entries format=format_name,duration,size -show_entries stream=codec_name,width,height -of default=noprint_wrappers=1 tests/studio/fixtures/sample-1s.mp4
# 期望：h264 / 320 / 240 / mov,mp4,... / duration=1.000000 / size=1948
```

把素材放进仓库是为了让视频链路的验证**可复现**（不依赖运行测试的机器上是否装有 ffmpeg）；如果后续要替换成更长的样例，请同步更新本文件的规格与 sha256。
