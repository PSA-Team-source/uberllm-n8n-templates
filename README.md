# UberLLM n8n video templates

Five [n8n](https://n8n.io) workflows that make AI videos with one
[UberLLM](https://uberllm.dev) API key: Veo 3.1, Kling 3.0, Hailuo, Nano Banana and
chat models through the same account and balance.

They use n8n's built-in **HTTP Request** node and one **Header Auth** credential, so they
run on n8n Cloud and self-hosted n8n with nothing to install. Each one starts the video,
waits for it, and gives you the file as binary `data`.

Every workflow here was imported into n8n 2.45.6 and run end to end against the live API
on 2026-10-30 (with short, low-resolution settings to keep the test cheap).

| File | What it does | Default run |
|---|---|---|
| [`veo-3-1-fast-text-to-video.json`](workflows/veo-3-1-fast-text-to-video.json) | A prompt becomes a Veo 3.1 Fast clip | 8 s, 720p, with sound |
| [`kling-image-to-video.json`](workflows/kling-image-to-video.json) | A picture becomes a Kling 3.0 clip (the picture is the first frame) | 5 s, no sound |
| [`faceless-shorts-pipeline.json`](workflows/faceless-shorts-pipeline.json) | A chat model writes a script, an image model draws each scene, Hailuo animates each one | 3 scenes of 6 s |
| [`ugc-product-ad.json`](workflows/ugc-product-ad.json) | From one product photo: Gemini plans the ad, Nano Banana puts the product in a creator's hand, Kling animates it | 5 s with sound |
| [`gemini-veo-replacement.json`](workflows/gemini-veo-replacement.json) | The popular "Gemini → Generate a video" workflow, moved to UberLLM after Google shut down the Veo 3.1 preview models in the Gemini API | 8 s, 720p, with sound |

What a run costs today, at each template's default settings, is on
[uberllm.dev/docs/n8n-video](https://uberllm.dev/docs/n8n-video) (read from the live price
list). Per-second prices for every video model: [uberllm.dev/fal-api-pricing](https://uberllm.dev/fal-api-pricing).

## Use one

1. In n8n: **⋯** (top right) → **Import from URL…** and paste the raw link, e.g.
   `https://raw.githubusercontent.com/PSA-Team-source/uberllm-n8n-templates/main/workflows/kling-image-to-video.json`
   (or download the file and use **Import from File…**).
2. Get a key at [uberllm.dev](https://uberllm.dev) (sign in with your email, add credit).
3. Open any UberLLM node → **Credential** → **Create new** → **Header Auth**:
   - **Name**: `Authorization`
   - **Value**: `Bearer ull_...`
4. Edit the **Settings** node and click **Execute workflow**.

## Coming from the Gemini Veo node

Google turned off `veo-3.1-generate-preview`, `veo-3.1-fast-generate-preview` and
`veo-3.1-lite-generate-preview` in the Gemini API on 2026-10-22
([notice](https://ai.google.dev/gemini-api/docs/veo)). `gemini-veo-replacement.json`
follows the same steps as the popular Gemini-based templates on n8n.io (a
configuration node, a prompt-writing call, the video call; see
[6557](https://n8n.io/workflows/6557), [11617](https://n8n.io/workflows/11617),
[13149](https://n8n.io/workflows/13149)). To move your own workflow, delete the Gemini
**Generate a video** node, paste the nodes from **Generate Video with Veo** to **Video**,
and swap the model name:

| Gemini model | UberLLM model |
|---|---|
| `veo-3.1-fast-generate-preview` | `fal-ai/veo3.1/fast` |
| `veo-3.1-generate-preview` | `google/veo3-1-full-text-to-video` |
| Veo 3.1 Fast from a picture | `fal-ai/veo3.1/fast/image-to-video` (add `image_url`) |

The **Video** node gives what the Gemini node gave: the file as binary `data` and a `url`.
That link needs your key and is kept about 3 hours, so a step that posts by link
(Instagram, Blotato) needs the file uploaded first, or add `output_upload_url` (a
presigned PUT link) to the start request and UberLLM uploads the file there.

## How they work

- **Start**: `POST https://api.uberllm.dev/v1/video/generations` answers at once with the
  job `id` and `max_cost_usd`. It sends an `Idempotency-Key` made from the n8n execution id,
  so a retry after a dropped connection gets the same job back instead of a second one.
- **Wait**: every 15 s the workflow asks `GET /v1/video/generations/{id}` until the job has
  `succeeded`. A job that ends `failed` or `expired` stops the workflow with the reason.
  After 60 checks it stops waiting (the job still finishes and shows in Dashboard → Activity).
- **Download**: the result link is fetched with the same credential into binary `data`.
- Every request sends `X-UberLLM-Source: n8n` and video jobs carry `metadata.template`, so
  your Activity list shows which workflow made each job.

API reference: [uberllm.dev/docs/media](https://uberllm.dev/docs/media).

## What you pay

- The most a job can cost is set before it starts: the start reply carries `max_cost_usd`,
  which is held from your balance, and the job is never billed more. Each **Settings** node
  has a `max_cost`; a job that could cost more is refused before anything is charged.
- A video that fails or runs out of time costs nothing: the whole hold comes back.
- Credit you buy does not expire while your account is open.
- No UberLLM fee on requests. Adding credit by card costs 5.5% + $0.50.

## Build the files

The workflow files are generated so all five stay consistent:

```sh
python3 tools/build.py            # reads live prices for the notes from the public models list
python3 tools/build.py --offline  # reuses tools/prices.json
python3 tools/check.py            # structure check: unique names, links, credentials, no keys in files
```

## Other UberLLM integrations

- n8n community node: [n8n-nodes-uberllm](https://github.com/PSA-Team-source/n8n-nodes-uberllm)
- ComfyUI nodes: [comfyui-uberllm](https://github.com/PSA-Team-source/comfyui-uberllm)

MIT license. UberLLM is not affiliated with Uber.
