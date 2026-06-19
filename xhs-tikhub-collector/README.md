# xhs-tikhub-collector

用 TikHub API 采集小红书笔记信息到本地。

参考文档：https://blog.tikhub.io/zh/article/7

## 准备

复制 `.env.example` 为 `.env`，填入你的 TikHub API Key：

```bash
cp .env.example .env
```

`.env` 内容：

```bash
TIKHUB_API_KEY=你的_TikHub_API_Key
```

## 使用

在本目录运行：

```bash
./xhs_collect.py "小红书分享文本或链接"
```

也可以指定输出目录：

```bash
./xhs_collect.py "https://www.xiaohongshu.com/discovery/item/..." --out ~/downloads/xhs
```

如果你知道是视频笔记：

```bash
./xhs_collect.py "小红书分享文本或链接" --type video
```

如果你想把返回 JSON 里识别到的图片、视频 URL 也下载下来：

```bash
./xhs_collect.py "小红书分享文本或链接" --download-media
```

加上 `--download-media` 时，脚本会在下载到视频或音频后自动生成一份适合语音转写的 MP3：

```text
media/作者昵称-帖子标题-audio.mp3
```

默认参数是 `mp3`、`16kHz`、单声道、`64k`。如果只想下载原始媒体，不抽音频：

```bash
./xhs_collect.py "小红书分享文本或链接" --download-media --no-extract-audio
```

## 全局命令

已经可以通过软链接做成全局命令：

```bash
xhs-collect "小红书分享文本或链接"
```

默认会保存到 `/home/ubuntu/repos/链接解析插件/采集文件夹`，每条笔记一个文件夹。

- 文件夹命名：`作者昵称-帖子标题`
- 数据文件命名：`作者昵称-帖子标题-summary.json`、`作者昵称-帖子标题-raw.json`
- 媒体文件命名：`作者昵称-帖子标题-video.mp4`、`作者昵称-帖子标题-cover.webp`
- 抽取音频：`media/作者昵称-帖子标题-audio.mp3`
- 本地媒体索引：`作者昵称-帖子标题-local_media.json`
- `cover`/`post_cover` 优先使用小红书帖子封面；`video_thumbnail` 是视频流缩略帧；`first_frame` 是视频首帧
- `media_urls.txt`：从响应中识别出的媒体 URL
- `media/`：只有加 `--download-media` 时才会创建

## 说明

脚本默认按文档推荐优先使用 App V2：

- 图文：`/api/v1/xiaohongshu/app_v2/get_image_note_detail`
- 视频：`/api/v1/xiaohongshu/app_v2/get_video_note_detail`
- 兜底：`/api/v1/xiaohongshu/app/get_note_info`

默认 `--type auto` 会先尝试图文接口，再尝试视频接口。TikHub 支持 `share_text` 参数，所以从小红书复制出来的一整段分享文本可以直接传入。

中国大陆网络如果访问 `api.tikhub.io` 不稳定，可以改用：

```bash
./xhs_collect.py "小红书分享文本或链接" --base-url https://api.tikhub.dev
```
