---
title: Vocals Remover
emoji: 🎵
colorFrom: purple
colorTo: indigo
sdk: gradio
sdk_version: 6.30.0
python_version: "3.10"
app_file: app.py
pinned: false
---

# Vocals Remover

Splits a song into an instrumental track and a vocals track using [Demucs](https://github.com/adefossez/demucs) (`htdemucs`). Runs on Hugging Face ZeroGPU.

- Accepts MP3, WAV, FLAC, M4A, OGG and AAC, up to 50 MB and 10 minutes.
- Returns two 320 kbps MP3 files.
- Uploads and results are deleted from the Space within about 30 minutes.

## API

The Space exposes one endpoint, `/separate`, which takes a song file and returns the instrumental and the vocals. Example with the JavaScript client:

```js
import { Client, handle_file } from '@gradio/client'

const client = await Client.connect('USERNAME/SPACE_NAME')
const result = await client.predict('/separate', { song: handle_file(file) })
const [instrumental, vocals] = result.data
```

## Limits

GPU time is shared and rationed per visitor per day by Hugging Face. When a visitor's quota runs out, the Space returns an error until the quota resets.
