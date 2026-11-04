# Sourced by enable-agent.sh and enable-ads.sh (needs $DOCKER and getvar).
# Sends the Postiz Agent's kind of request (Responses API, with a tool) through the AI translator
# and prints one line saying whether the Agent will get an answer, and from which model.
agent_check() {
  [ -n "$(getvar LITELLM_MASTER_KEY)" ] || return 0
  printf "Postiz Agent check: "
  $DOCKER exec -e MK="$(getvar LITELLM_MASTER_KEY)" postiz-ai python -c "
import json, os, time, urllib.request
body = {'model': 'gpt-5.2', 'max_output_tokens': 400,
        'input': [{'role': 'developer', 'content': 'You help schedule social posts.'},
                  {'role': 'user', 'content': [{'type': 'input_text', 'text': 'Say hello in five words or fewer.'}]}],
        'tools': [{'type': 'function', 'name': 'integrationList', 'description': 'List channels',
                   'parameters': {'type': 'object', 'properties': {}}}]}
for attempt in range(8):
    req = urllib.request.Request('http://localhost:4000/v1/responses', data=json.dumps(body).encode(),
        headers={'Authorization': 'Bearer ' + os.environ['MK'], 'Content-Type': 'application/json'})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=120))
        text = ' '.join(c.get('text', '') for o in r.get('output', []) if o.get('type') == 'message' for c in o.get('content', []))
        print('working (' + str(r.get('model')) + '): ' + (' '.join(text.split())[:60] or 'replied'))
        break
    except urllib.error.HTTPError as e:
        print('NOT working - ' + str(e.code) + ' ' + e.read().decode()[:300].replace(chr(10), ' '))
        break
    except Exception as e:
        time.sleep(5)
else:
    print('the AI translator is still starting; check again in a minute')
" 2>/dev/null || echo "couldn't reach the AI translator (is AI turned on?)"
}
