# douyin-tikhub-collector

用 TikHub API 采集抖音作品信息到本地，处理逻辑和小红书采集脚本保持一致。

默认读取：

- 当前目录 `.env`
- 或兄弟目录 `../xhs-tikhub-collector/.env`

所以你已经给小红书脚本配置过 `TIKHUB_API_KEY` 的话，抖音脚本可以直接用。

## 使用

```bash
douyin-collect "抖音分享文本或链接"
```

下载封面和视频：

```bash
douyin-collect "抖音分享文本或链接" --download-media
```

加上 `--download-media` 时，脚本会在下载到视频或音频后自动生成一份适合语音转写的 MP3：

```text
media/作者昵称-作品标题-audio.mp3
```

默认参数是 `mp3`、`16kHz`、单声道、`64k`。如果只想下载原始媒体，不抽音频：

```bash
douyin-collect "抖音分享文本或链接" --download-media --no-extract-audio
```

如果要优先请求 TikHub 的最高画质播放链接接口：

```bash
douyin-collect "抖音链接或 aweme_id" --highest-quality --download-media
```

注意：最高画质接口在 TikHub 文档里标注可能额外计费，所以默认不开。

## 默认保存位置

```text
/home/ubuntu/repos/链接解析插件/采集文件夹
```

命名规则：

- 文件夹：`作者昵称-作品标题`
- 数据文件：`作者昵称-作品标题-summary.json`、`作者昵称-作品标题-raw.json`
- 媒体文件：`作者昵称-作品标题-video.mp4`、`作者昵称-作品标题-cover.jpg`
- 抽取音频：`media/作者昵称-作品标题-audio.mp3`
- 本地媒体索引：`作者昵称-作品标题-local_media.json`

## 当前接口

默认域名：

```text
https://api.tikhub.io
```

接口：

```text
GET /api/v1/douyin/app/v3/fetch_one_video_by_share_url
GET /api/v1/douyin/app/v3/fetch_one_video_v3
GET /api/v1/douyin/app/v3/fetch_video_highest_quality_play_url
```
