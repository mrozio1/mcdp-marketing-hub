#!/bin/sh
# Check that the AI translator can write text AND generate an image, and show
# the provider's exact error if not. Usage: sh test-ai.sh
set -eu
cd "$(dirname "$0")"
. ./profiles.sh
master=$(grep -E '^LITELLM_MASTER_KEY=' .env | cut -d= -f2- || true)
[ -n "$master" ] || { echo "AI isn't enabled yet. Run: sh enable-ai.sh YOUR_KEY"; exit 1; }
echo "Text model:  $(grep -E '^AI_TEXT_MODEL=' .env | cut -d= -f2-)"
echo "Image model: $(grep -E '^AI_IMAGE_MODEL=' .env | cut -d= -f2-)"
$DOCKER exec postiz-ai python -c "
import json, urllib.request
H = {'Authorization': 'Bearer $master', 'Content-Type': 'application/json'}
def call(path, body):
    req = urllib.request.Request('http://localhost:4000/v1/' + path, data=json.dumps(body).encode(), headers=H)
    try:
        return json.load(urllib.request.urlopen(req, timeout=120)), None
    except urllib.error.HTTPError as e:
        return None, '%s %s' % (e.code, e.read().decode()[:500].replace(chr(10), ' '))
    except Exception as e:
        return None, str(e)
r, err = call('chat/completions', {'model': 'gpt-4.1', 'max_tokens': 200,
    'messages': [{'role': 'user', 'content': 'Write a 6-word headline for a dental office.'}]})
print('TEXT : ' + ('OK  -> ' + ((r['choices'][0]['message'].get('content') or '').strip().splitlines() or [''])[0][:80] if r else 'FAIL -> ' + err))
r, err = call('images/generations', {'model': 'chatgpt-image-latest', 'prompt': 'a friendly cartoon tooth giving a thumbs up', 'size': '1024x1024'})
ok = r and r.get('data') and (r['data'][0].get('b64_json') or r['data'][0].get('url'))
print('IMAGE: ' + ('OK  -> image received (%d KB)' % (len(r['data'][0].get('b64_json') or '') * 3 // 4 // 1024) if ok else 'FAIL -> ' + (err or json.dumps(r)[:300])))
"
