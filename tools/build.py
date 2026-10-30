#!/usr/bin/env python3
"""Builds the n8n workflow files in ../workflows from one place.

Every workflow calls the UberLLM API with n8n's own HTTP Request node and one
Header Auth credential, so it needs no community node. Prices in the notes are
read from the public models list when this runs:

    python3 tools/build.py            # reads https://api.uberllm.dev/api/v1/public/models
    python3 tools/build.py --offline  # keeps the prices from the last build (tools/prices.json)
"""
import json
import sys
import urllib.request
import uuid
from datetime import date, timezone, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'workflows'
PRICES = ROOT / 'tools' / 'prices.json'
API = 'https://api.uberllm.dev'
FEE = 'No UberLLM fee on requests. Adding credit by card costs 5.5% + $0.50.'

# The one credential every HTTP node uses. Open the workflow, pick (or create)
# a Header Auth credential with Name "Authorization" and Value "Bearer <key>".
CRED = {'httpHeaderAuth': {'id': 'uberllmApiKey01', 'name': 'UberLLM API key'}}

MODELS_USED = [
    'fal-ai/veo3.1/fast', 'fal-ai/veo3.1/fast/image-to-video', 'google/veo3-1-full-text-to-video',
    'fal-ai/kling-video/v3/standard/image-to-video', 'fal-ai/kling-video/v3/pro/image-to-video',
    'fal-ai/minimax/hailuo-02/standard/image-to-video', 'venice/z-image-turbo',
    'google/nano-banana-2-edit', 'deepseek/deepseek-v3.2', 'google/gemini-2.5-flash',
]


def load_prices(offline: bool) -> dict:
    if offline:
        return json.loads(PRICES.read_text())
    req = urllib.request.Request(f'{API}/api/v1/public/models', headers={'User-Agent': 'uberllm-n8n-templates'})
    with urllib.request.urlopen(req, timeout=30) as r:
        models = {m['id']: m for m in json.load(r)['models']}
    out = {'checked': datetime.now(timezone.utc).strftime('%Y-%m-%d'), 'models': {}}
    for mid in MODELS_USED:
        m = models.get(mid)
        if not m:
            continue
        out['models'][mid] = {
            'name': m.get('display_name') or mid,
            'unit': m.get('media_unit'),
            'usd': m.get('media_price_now_usd'),
            'rates': m.get('media_rates'),
            'price_now': m.get('price_now'),
        }
    PRICES.write_text(json.dumps(out, indent=2) + '\n')
    return out


def per_second(prices: dict, mid: str, resolution=None, audio=False):
    m = prices['models'].get(mid)
    if not m:
        return None
    for r in m.get('rates') or []:
        if r['audio'] == audio and (resolution is None or (r.get('resolution') or '').lower() == resolution.lower()):
            return r['usd_per_second']
    return m['usd'] if m.get('unit') == 'second' and not audio else None


def usd(x: float) -> str:
    s = f'{x:.4f}'.rstrip('0')
    return '$' + (s + '0' if s.endswith('.') else s)


# ---------------------------------------------------------------- node helpers

class WF:
    def __init__(self, slug: str, name: str):
        self.slug, self.name = slug, name
        self.nodes, self.conn = [], {}

    def _id(self, name: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f'uberllm-n8n/{self.slug}/{name}'))

    def add(self, name, type_, version, pos, params, **extra):
        node = {'parameters': params, 'id': self._id(name), 'name': name, 'type': type_,
                'typeVersion': version, 'position': list(pos)}
        node.update(extra)
        self.nodes.append(node)
        return name

    def link(self, a, b, out=0, inp=0):
        main = self.conn.setdefault(a, {'main': []})['main']
        while len(main) <= out:
            main.append([])
        main[out].append({'node': b, 'type': 'main', 'index': inp})

    def chain(self, *names):
        for a, b in zip(names, names[1:]):
            self.link(a, b)

    def note(self, name, pos, w, h, text, color=None):
        p = {'content': text, 'height': h, 'width': w}
        if color:
            p['color'] = color
        return self.add(name, 'n8n-nodes-base.stickyNote', 1, pos, p)

    def write(self, description: str):
        doc = {
            'name': self.name,
            'nodes': self.nodes,
            'connections': self.conn,
            'pinData': {},
            'settings': {'executionOrder': 'v1'},
            'meta': {'templateCredsSetupCompleted': False, 'description': description},
            'tags': [],
        }
        (OUT / f'{self.slug}.json').write_text(json.dumps(doc, indent=2, ensure_ascii=False) + '\n')


def manual(wf, pos=(0, 0)):
    return wf.add('Run workflow', 'n8n-nodes-base.manualTrigger', 1, pos, {})


def settings(wf, name, pos, fields):
    """fields: list of (name, value, type)."""
    return wf.add(name, 'n8n-nodes-base.set', 3.4, pos, {
        'mode': 'manual',
        'assignments': {'assignments': [
            {'id': wf._id(f'{name}/{k}'), 'name': k, 'value': v, 'type': t} for k, v, t in fields]},
        'includeOtherFields': False,
        'options': {},
    })


def http(wf, name, pos, method, url, body_expr=None, headers=None, options=None, retry=True):
    p = {'method': method, 'url': url, 'authentication': 'genericCredentialType',
         'genericAuthType': 'httpHeaderAuth', 'sendHeaders': True,
         # Names the app on the request's line in Dashboard -> Activity.
         'headerParameters': {'parameters': [{'name': 'X-UberLLM-Source', 'value': 'n8n'}]
                              + [{'name': k, 'value': v} for k, v in (headers or [])]}}
    if body_expr is not None:
        p['sendBody'] = True
        p['specifyBody'] = 'json'
        p['jsonBody'] = body_expr
    p['options'] = options or {}
    extra = {'credentials': CRED}
    if retry:
        extra.update(retryOnFail=True, maxTries=3, waitBetweenTries=5000)
    return wf.add(name, 'n8n-nodes-base.httpRequest', 4.2, pos, p, **extra)


def if_node(wf, name, pos, left, op, right=None):
    cond = {'id': wf._id(f'{name}/c'), 'leftValue': left, 'operator': op}
    if right is not None:
        cond['rightValue'] = right
    return wf.add(name, 'n8n-nodes-base.if', 2.2, pos, {
        'conditions': {
            'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
            'conditions': [cond],
            'combinator': 'and',
        },
        'options': {},
    })


def code(wf, name, pos, js, mode=None):
    p = {'jsCode': js}
    if mode:
        p['mode'] = mode
    return wf.add(name, 'n8n-nodes-base.code', 2, pos, p)


def wait_job(wf, start, x, y, label='video', max_polls=60, seconds=15):
    """Wait -> Get job -> ready? / still running? loop. Returns (ready_node, entry)."""
    w = wf.add(f'Wait for {label}', 'n8n-nodes-base.wait', 1.1, (x, y),
               {'amount': seconds, 'unit': 'seconds'}, webhookId=wf._id(f'wait-{label}'))
    g = http(wf, f'Check {label} job', (x + 220, y), 'GET',
             f"={{{{ '{API}/v1/video/generations/' + $json.id }}}}")
    ready = if_node(wf, f'{label.capitalize()} ready?', (x + 440, y), '={{ $json.status }}',
                    {'type': 'string', 'operation': 'equals'}, 'succeeded')
    running = if_node(wf, f'Still making {label}?', (x + 660, y + 160),
                      f"={{{{ ['queued', 'running'].includes($json.status) && $runIndex < {max_polls} }}}}",
                      {'type': 'boolean', 'operation': 'true', 'singleValue': True})
    stop = wf.add(f'{label.capitalize()} not made', 'n8n-nodes-base.stopAndError', 1, (x + 880, y + 260), {
        'errorMessage': ("={{ 'The " + label + " job ' + $json.id + ' ended as ' + $json.status + ': ' + "
                         "($json.error && $json.error.message ? $json.error.message : 'it took too long to wait for') + "
                         "'. A failed or expired job costs nothing.' }}"),
    })
    wf.link(start, w)
    wf.chain(w, g, ready)
    wf.link(ready, running, out=1)
    wf.link(running, w, out=0)
    wf.link(running, stop, out=1)
    return ready


def result_and_download(wf, ready, x, y, label='video', extra_fields=(), out_name='Video'):
    """Result fields, then the file as binary `data` (the download keeps the fields)."""
    fields = [
        ('job_id', '={{ $json.id }}', 'string'),
        ('model', '={{ $json.model }}', 'string'),
        ('url', '={{ $json.results[0].url }}', 'string'),
        ('cost_usd', '={{ $json.usage && $json.usage.cost_usd != null ? $json.usage.cost_usd : null }}', 'number'),
        ('cost_pending', '={{ !!($json.usage && $json.usage.cost_pending) }}', 'boolean'),
    ] + list(extra_fields)
    res = settings(wf, f'{label.capitalize()} result', (x, y), fields)
    dl = http(wf, out_name, (x + 220, y), 'GET', '={{ $json.url }}',
              options={'response': {'response': {'responseFormat': 'file', 'outputPropertyName': 'data'}},
                       'timeout': 300000})
    wf.link(ready, res, out=0)
    wf.link(res, dl)
    return dl


def chat_body(model_expr, system, user_expr, json_mode=True, max_tokens=900, image_expr=None):
    user = user_expr if image_expr is None else (
        f"[{{ type: 'text', text: {user_expr} }}, {{ type: 'image_url', image_url: {{ url: {image_expr} }} }}]")
    body = (f"={{{{ JSON.stringify({{ model: {model_expr}, max_tokens: {max_tokens}, temperature: 0.8, "
            f"messages: [{{ role: 'system', content: {json.dumps(system)} }}, {{ role: 'user', content: {user} }}]"
            + (", response_format: { type: 'json_object' }" if json_mode else '') + ' }) }}')
    return body


PARSE_JSON = r"""
// The model was asked for JSON. Read it even if it wrapped it in a code fence.
const text = $json.choices?.[0]?.message?.content ?? '';
const match = text.match(/\{[\s\S]*\}/);
if (!match) throw new Error('The chat model did not answer with JSON: ' + text.slice(0, 200));
return { json: JSON.parse(match[0]) };
""".strip()


SETUP = (
    "### Setup (2 minutes)\n"
    "1. Get a key at https://uberllm.dev (sign in with your email, add credit).\n"
    "2. Open any **UberLLM** HTTP node → Credential → **Create new** → *Header Auth*:\n"
    "   - Name: `Authorization`\n"
    "   - Value: `Bearer YOUR_UBERLLM_KEY`\n"
    "   Every UberLLM node in this workflow uses this one credential.\n"
    "3. Edit the **Settings** node, then click **Execute workflow**.\n\n"
)

PROMISES = (
    "### What you pay\n"
    "- The job's cost is shown before it starts (`max_cost_usd` on the reply) and held from your balance. "
    "It is never billed more than that.\n"
    "- A video that fails or runs out of time costs nothing: the whole hold comes back.\n"
    "- Credit you buy does not expire.\n"
    f"- {FEE}\n"
)

FILES = (
    "### The file\n"
    "The last node has the video as binary `data` (send it to Drive, S3, YouTube…) and its link in `url`. "
    "The link needs your key and is kept about 3 hours. To have UberLLM put the file in your own bucket, "
    "add `output_upload_url` (a presigned PUT link) to the start request.\n"
)


def video_price_line(prices, mid, res, audio, seconds):
    r = per_second(prices, mid, res, audio)
    if r is None:
        return ''
    return (f"{usd(r)} per second" + (f" at {res}" if res else '') + (' with sound' if audio else ' without sound')
            + f" → {usd(r * seconds)} for {seconds} s (price on {prices['checked']}; live prices: https://uberllm.dev/fal-api-pricing)")


# ------------------------------------------------------------------ templates

def t_text_to_video(prices):
    wf = WF('veo-3-1-fast-text-to-video', 'UberLLM: text to video with Veo 3.1 Fast')
    m = 'fal-ai/veo3.1/fast'
    wf.note('About', (-560, -260), 520, 760,
            "## Text to video with Veo 3.1 Fast (UberLLM)\n"
            "Writes a video from a prompt with Google's Veo 3.1 Fast, waits for it, and hands you the file.\n\n"
            + SETUP +
            f"### Price\n{video_price_line(prices, m, '720p', True, 8)}\n"
            f"Without sound: {video_price_line(prices, m, '720p', False, 8)}\n\n" + PROMISES + "\n" + FILES)
    t = manual(wf, (0, 0))
    s = settings(wf, 'Settings', (220, 0), [
        ('prompt', 'A slow dolly shot through a neon-lit night market in the rain, steam rising from food stalls, cinematic', 'string'),
        ('model', m, 'string'),
        ('duration_seconds', 8, 'number'),
        ('resolution', '720p', 'string'),
        ('aspect_ratio', '16:9', 'string'),
        ('audio', True, 'boolean'),
        ('max_cost', 1.5, 'number'),
    ])
    start = http(wf, 'Start video', (440, 0), 'POST', f'{API}/v1/video/generations',
                 "={{ JSON.stringify({ model: $json.model, prompt: $json.prompt, duration_seconds: $json.duration_seconds, "
                 "resolution: $json.resolution, aspect_ratio: $json.aspect_ratio, audio: $json.audio, max_cost: $json.max_cost, metadata: { template: 'veo-3-1-fast-text-to-video' } }) }}",
                 headers=[('Idempotency-Key', '={{ $execution.id }}-video')])
    wf.chain(t, s, start)
    ready = wait_job(wf, start, 660, 0)
    result_and_download(wf, ready, 1320, -200)
    wf.write('Make a video from a text prompt with Veo 3.1 Fast through the UberLLM API: start the job, wait for it, download the file.')


def t_image_to_video(prices):
    wf = WF('kling-image-to-video', 'UberLLM: image to video with Kling 3.0')
    m = 'fal-ai/kling-video/v3/standard/image-to-video'
    pro = 'fal-ai/kling-video/v3/pro/image-to-video'
    wf.note('About', (-560, -260), 520, 800,
            "## Image to video with Kling 3.0 (UberLLM)\n"
            "Turns a picture into a video clip: the picture is the first frame and the prompt says what moves.\n\n"
            + SETUP +
            "`image_url` must be a public `https://` link (or a `data:` URI). Your own file from an earlier "
            "UberLLM image works too.\n\n"
            f"### Price\nKling 3.0 Standard: {video_price_line(prices, m, None, False, 5)}\n"
            f"Kling 3.0 Pro (`{pro}`): {video_price_line(prices, pro, None, False, 5)}\n\n"
            + PROMISES + "\n" + FILES)
    t = manual(wf, (0, 0))
    s = settings(wf, 'Settings', (220, 0), [
        ('image_url', 'https://raw.githubusercontent.com/PSA-Team-source/uberllm-n8n-templates/main/samples/scene.jpg', 'string'),
        ('prompt', 'The camera slowly pushes in while rain runs down the window and the lamp light flickers', 'string'),
        ('model', m, 'string'),
        ('duration_seconds', 5, 'number'),
        ('audio', False, 'boolean'),
        ('max_cost', 0.75, 'number'),
    ])
    start = http(wf, 'Start video', (440, 0), 'POST', f'{API}/v1/video/generations',
                 "={{ JSON.stringify({ model: $json.model, prompt: $json.prompt, image_url: $json.image_url, "
                 "duration_seconds: $json.duration_seconds, audio: $json.audio, max_cost: $json.max_cost, metadata: { template: 'kling-image-to-video' } }) }}",
                 headers=[('Idempotency-Key', '={{ $execution.id }}-video')])
    wf.chain(t, s, start)
    ready = wait_job(wf, start, 660, 0)
    result_and_download(wf, ready, 1320, -200)
    wf.write('Turn a picture into a video with Kling 3.0 through the UberLLM API: start the job, wait for it, download the file.')


SHORTS_SYSTEM = (
    "You write faceless short-form videos (YouTube Shorts, TikTok, Reels). Answer with JSON only: "
    '{"title": string, "caption": string, "hashtags": string, "scenes": [{"narration": string, '
    '"image_prompt": string, "motion_prompt": string}]}. Each scene is one vertical shot of about 6 seconds. '
    "image_prompt describes one still frame in detail (subject, setting, light, style, vertical 9:16, no text in the picture). "
    "motion_prompt says how the camera and subject move in that shot. narration is one short spoken line."
)

SPLIT_SCENES = r"""
// One item per scene, at most the number of scenes asked for in Settings.
const text = $json.choices?.[0]?.message?.content ?? '';
const match = text.match(/\{[\s\S]*\}/);
if (!match) throw new Error('The chat model did not answer with JSON: ' + text.slice(0, 200));
const script = JSON.parse(match[0]);
const want = Number($('Settings').first().json.scenes) || 3;
const scenes = (script.scenes || []).slice(0, want);
if (!scenes.length) throw new Error('The script has no scenes.');
return scenes.map((s, i) => ({ json: { scene: i + 1, ...s } }));
""".strip()

SHORTS_PACKAGE = r"""
// One item: the script, every clip's link and cost, and each clip as binary scene_1, scene_2, ...
const script = JSON.parse($('Write script').first().json.choices[0].message.content.match(/\{[\s\S]*\}/)[0]);
const clips = [];
const binary = {};
for (const item of $input.all()) {
  const n = Number(item.json.scene);
  clips.push({ scene: n, narration: item.json.narration, url: item.json.url, cost_usd: item.json.cost_usd, job_id: item.json.job_id });
  if (item.binary && item.binary.data) binary[`scene_${n}`] = item.binary.data;
}
clips.sort((a, b) => a.scene - b.scene);
const total = clips.reduce((s, c) => s + (Number(c.cost_usd) || 0), 0);
return [{ json: { title: script.title, caption: script.caption, hashtags: script.hashtags, clips, video_cost_usd: Math.round(total * 10000) / 10000 }, binary }];
""".strip()


def t_shorts(prices):
    wf = WF('faceless-shorts-pipeline', 'UberLLM: faceless Shorts pipeline (script → images → video clips)')
    chat, img, vid = 'deepseek/deepseek-v3.2', 'venice/z-image-turbo', 'fal-ai/minimax/hailuo-02/standard/image-to-video'
    ip = prices['models'].get(img, {}).get('usd')
    vr = per_second(prices, vid, '768p', False)
    per_scene = (ip or 0) + (vr or 0) * 6
    wf.note('About', (-600, -300), 560, 980,
            "## Faceless Shorts pipeline (UberLLM)\n"
            "One key does all three steps:\n"
            f"1. **Write script**: a cheap chat model (`{chat}`) writes the title, caption, hashtags and the scenes.\n"
            f"2. **Make scene image**: `{img}` draws each scene as a vertical picture.\n"
            f"3. **Start scene video**: `{vid}` animates each picture into a ~6 s clip.\n"
            "Scenes run one after another, so a run with 3 scenes takes a few minutes.\n\n"
            + SETUP +
            "### Price per scene\n"
            + (f"Image {usd(ip)} + 6 s clip at 768P {usd(vr * 6)} = about {usd(per_scene)} per scene; "
               f"the script costs a fraction of a cent (prices on {prices['checked']}).\n\n" if ip and vr else '')
            + "### Output\nOne item: `title`, `caption`, `hashtags`, `clips[]` (narration, link, cost) and each clip as "
            "binary `scene_1`, `scene_2`… Join them with your editor or an ffmpeg step, add the narration with a speech model "
            "(`POST /v1/audio/speech`, same key), and post.\n\n" + PROMISES)
    t = manual(wf, (0, 0))
    s = settings(wf, 'Settings', (220, 0), [
        ('topic', '3 surprising facts about octopuses', 'string'),
        ('scenes', 3, 'number'),
        ('chat_model', chat, 'string'),
        ('image_model', img, 'string'),
        ('video_model', vid, 'string'),
        ('duration_seconds', 6, 'number'),
        ('resolution', '768P', 'string'),
        ('max_cost_per_clip', 0.5, 'number'),
    ])
    w = http(wf, 'Write script', (440, 0), 'POST', f'{API}/v1/chat/completions',
             chat_body('$json.chat_model', SHORTS_SYSTEM,
                       "'Topic: ' + $json.topic + '. Write exactly ' + $json.scenes + ' scenes.'", max_tokens=1500),
             options={'timeout': 120000})
    sp = code(wf, 'Split scenes', (660, 0), SPLIT_SCENES)
    loop = wf.add('One scene at a time', 'n8n-nodes-base.splitInBatches', 3, (880, 0), {'options': {}})
    im = http(wf, 'Make scene image', (1100, 100), 'POST', f'{API}/v1/images/generations',
              "={{ JSON.stringify({ model: $('Settings').first().json.image_model, prompt: $json.image_prompt, n: 1, size: '1024x1536' }) }}",
              options={'timeout': 120000})
    sv = http(wf, 'Start scene video', (1320, 100), 'POST', f'{API}/v1/video/generations',
              "={{ JSON.stringify({ model: $('Settings').first().json.video_model, prompt: $('One scene at a time').item.json.motion_prompt, "
              "image_url: $json.data[0].url, duration_seconds: $('Settings').first().json.duration_seconds, "
              "resolution: $('Settings').first().json.resolution, max_cost: $('Settings').first().json.max_cost_per_clip, "
              "metadata: { template: 'faceless-shorts-pipeline', scene: String($('One scene at a time').item.json.scene) } }) }}",
              headers=[('Idempotency-Key', "={{ $execution.id }}-scene-{{ $('One scene at a time').item.json.scene }}")])
    wf.chain(t, s, w, sp, loop)
    wf.link(loop, im, out=1)
    wf.link(im, sv)
    ready = wait_job(wf, sv, 1540, 100, label='clip')
    mg = result_and_download(wf, ready, 2200, -100, label='clip', out_name='Clip', extra_fields=[
        ('scene', '={{ Number($json.metadata.scene) }}', 'number'),
        ('narration', "={{ $('Split scenes').all().find(s => String(s.json.scene) === $json.metadata.scene).json.narration }}", 'string'),
    ])
    wf.link(mg, loop)
    pk = code(wf, 'Shorts package', (1100, -200), SHORTS_PACKAGE)
    wf.link(loop, pk, out=0)
    wf.write('Faceless Shorts with one UberLLM key: a cheap chat model writes the script, an image model draws each scene, a video model animates each one.')


UGC_SYSTEM = (
    "You plan short UGC-style product ads (a real person filming on a phone). Look at the product photo and answer "
    'with JSON only: {"scene_prompt": string, "motion_prompt": string, "hook": string, "caption": string}. '
    "scene_prompt: an edit instruction that puts this exact product (keep its label, shape and colours) in the hand of a "
    "creator who fits the audience, filming a selfie-style vertical shot in a real home setting, natural light, phone-camera look. "
    "motion_prompt: how the person and camera move in a 5-second clip (e.g. lifts the product to the camera and smiles). "
    "hook: the first spoken line. caption: the post caption."
)


def t_ugc(prices):
    wf = WF('ugc-product-ad', 'UberLLM: UGC-style product ad from one product photo')
    vis, ed, vid = 'google/gemini-2.5-flash', 'google/nano-banana-2-edit', 'fal-ai/kling-video/v3/standard/image-to-video'
    ep = prices['models'].get(ed, {}).get('usd')
    vr = per_second(prices, vid, None, True)
    wf.note('About', (-600, -300), 560, 980,
            "## UGC-style product ad (UberLLM)\n"
            "From one product photo to a vertical ad clip of a person holding your product:\n"
            f"1. **Plan the ad**: a vision model (`{vis}`) looks at the photo and writes the scene, the motion, a hook and a caption.\n"
            f"2. **Make UGC scene**: `{ed}` puts your product in a creator's hand (the label stays as it is).\n"
            f"3. **Start ad video**: `{vid}` turns that picture into a 5 s clip with sound.\n\n"
            + SETUP +
            "`product_image_url` must be a public `https://` link to the product photo (a plain background works best). "
            "If you change `vision_model`, pick one that reads pictures from a link (`google/gemini-2.5-flash` does).\n\n"
            "### Price\n"
            + (f"Scene picture {usd(ep)} + 5 s clip with sound {usd(vr * 5)} = about {usd(ep + vr * 5)} per ad "
               f"(prices on {prices['checked']}). The planning call costs a fraction of a cent.\n\n" if ep and vr else '')
            + PROMISES + "\n" + FILES)
    t = manual(wf, (0, 0))
    s = settings(wf, 'Settings', (220, 0), [
        ('product_image_url', 'https://raw.githubusercontent.com/PSA-Team-source/uberllm-n8n-templates/main/samples/product.jpg', 'string'),
        ('product_name', 'Amber glass serum bottle', 'string'),
        ('audience', 'women 25-40 who care about skincare', 'string'),
        ('vision_model', vis, 'string'),
        ('image_model', ed, 'string'),
        ('video_model', vid, 'string'),
        ('duration_seconds', 5, 'number'),
        ('audio', True, 'boolean'),
        ('max_cost', 1, 'number'),
    ])
    plan = http(wf, 'Plan the ad', (440, 0), 'POST', f'{API}/v1/chat/completions',
                chat_body('$json.vision_model', UGC_SYSTEM,
                          "'Product: ' + $json.product_name + '. Audience: ' + $json.audience + '.'",
                          image_expr='$json.product_image_url', max_tokens=2000),
                options={'timeout': 120000})
    parse = code(wf, 'Read the plan', (660, 0), PARSE_JSON, mode='runOnceForEachItem')
    scene = http(wf, 'Make UGC scene', (880, 0), 'POST', f'{API}/v1/images/edits',
                 "={{ JSON.stringify({ model: $('Settings').item.json.image_model, prompt: $json.scene_prompt, "
                 "image_url: $('Settings').item.json.product_image_url, aspect_ratio: '9:16', n: 1 }) }}",
                 options={'timeout': 180000})
    start = http(wf, 'Start ad video', (1100, 0), 'POST', f'{API}/v1/video/generations',
                 "={{ JSON.stringify({ model: $('Settings').item.json.video_model, prompt: $('Read the plan').item.json.motion_prompt, "
                 "image_url: $json.data[0].url, duration_seconds: $('Settings').item.json.duration_seconds, "
                 "audio: $('Settings').item.json.audio, max_cost: $('Settings').item.json.max_cost, metadata: { template: 'ugc-product-ad' } }) }}",
                 headers=[('Idempotency-Key', '={{ $execution.id }}-ad')])
    wf.chain(t, s, plan, parse, scene, start)
    ready = wait_job(wf, start, 1320, 0)
    result_and_download(wf, ready, 1980, -200, out_name='Ad video', extra_fields=[
        ('hook', "={{ $('Read the plan').first().json.hook }}", 'string'),
        ('caption', "={{ $('Read the plan').first().json.caption }}", 'string'),
        ('scene_image_url', "={{ $('Make UGC scene').first().json.data[0].url }}", 'string'),
    ])
    wf.write('UGC-style product ad from one product photo with one UberLLM key: a vision model plans the ad, an image model puts the product in a creator\'s hand, Kling animates it.')


VEO_PROMPT_SYSTEM = (
    "You write prompts for Google's Veo video model. Given a topic, write one detailed prompt for a short vertical video: "
    "subject, setting, camera movement, lighting, mood and sound. Answer with the prompt only, no preamble."
)


def t_gemini_replacement(prices):
    wf = WF('gemini-veo-replacement', 'Generate AI videos with Veo 3.1 after the Gemini API shutdown (UberLLM drop-in)')
    fast, full, i2v = 'fal-ai/veo3.1/fast', 'google/veo3-1-full-text-to-video', 'fal-ai/veo3.1/fast/image-to-video'
    chat = 'deepseek/deepseek-v3.2'
    fr = prices['models'].get(full, {}).get('usd')
    wf.note('About', (-640, -320), 600, 1180,
            "## Veo 3.1 after the Gemini API shutdown\n"
            "Google turned off the Veo 3.1 preview models in the Gemini API on 22 October 2026 "
            "(https://ai.google.dev/gemini-api/docs/veo), so workflows built on the **Google Gemini → Generate a video** "
            "node stopped working. This is the same workflow on UberLLM: same steps, same output, one key.\n\n"
            "### Swap the model names\n"
            f"| Gemini model | UberLLM model |\n|---|---|\n"
            f"| `veo-3.1-fast-generate-preview` | `{fast}` |\n"
            f"| `veo-3.1-generate-preview` | `{full}` |\n"
            f"| Veo 3.1 Fast, from a picture | `{i2v}` (add `image_url`) |\n\n"
            "### Move your own workflow\n"
            "Delete the Gemini **Generate a video** node and paste the nodes from **Generate Video with Veo** to **Video** "
            "in its place. **Video** gives what the Gemini node gave: the file as binary `data`, and `url`. "
            "The UberLLM link needs your key, so a step that posts by link (Instagram, Blotato) needs the file "
            "uploaded first (Drive, S3), or add `output_upload_url` to the start request.\n\n"
            + SETUP +
            "### Price\n"
            f"Veo 3.1 Fast: {video_price_line(prices, fast, '720p', True, 8)}\n"
            + (f"Veo 3.1: {usd(fr)} per second.\n" if fr else '')
            + "\n" + PROMISES)
    t = manual(wf, (0, 0))
    s = settings(wf, 'Workflow Configuration', (220, 0), [
        ('videoTopic', 'Create an engaging short video about daily motivation', 'string'),
        ('aspectRatio', '9:16', 'string'),
        ('durationSeconds', 8, 'number'),
        ('resolution', '720p', 'string'),
        ('audio', True, 'boolean'),
        ('promptModel', chat, 'string'),
        ('videoModel', fast, 'string'),
        ('maxCost', 1.5, 'number'),
    ])
    p = http(wf, 'Generate Video Prompt', (440, 0), 'POST', f'{API}/v1/chat/completions',
             chat_body('$json.promptModel', VEO_PROMPT_SYSTEM, "'Topic: ' + $json.videoTopic", json_mode=False, max_tokens=400),
             options={'timeout': 120000})
    start = http(wf, 'Generate Video with Veo', (660, 0), 'POST', f'{API}/v1/video/generations',
                 "={{ JSON.stringify({ model: $('Workflow Configuration').item.json.videoModel, "
                 "prompt: $json.choices[0].message.content.trim(), "
                 "aspect_ratio: $('Workflow Configuration').item.json.aspectRatio, "
                 "duration_seconds: $('Workflow Configuration').item.json.durationSeconds, "
                 "resolution: $('Workflow Configuration').item.json.resolution, "
                 "audio: $('Workflow Configuration').item.json.audio, "
                 "max_cost: $('Workflow Configuration').item.json.maxCost, metadata: { template: 'gemini-veo-replacement' } }) }}",
                 headers=[('Idempotency-Key', '={{ $execution.id }}-veo')])
    wf.chain(t, s, p, start)
    ready = wait_job(wf, start, 880, 0)
    result_and_download(wf, ready, 1540, -200, out_name='Video')
    wf.write('Drop-in replacement for the Google Gemini "Generate a video" (Veo) node after Google shut down the Veo 3.1 preview models in the Gemini API on 2026-10-22.')


def main():
    offline = '--offline' in sys.argv
    prices = load_prices(offline)
    OUT.mkdir(exist_ok=True)
    for f in (t_text_to_video, t_image_to_video, t_shorts, t_ugc, t_gemini_replacement):
        f(prices)
    print('built', len(list(OUT.glob('*.json'))), 'workflows; prices checked', prices['checked'])


if __name__ == '__main__':
    main()
