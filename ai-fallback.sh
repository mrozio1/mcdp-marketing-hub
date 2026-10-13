# Sourced by enable-ai.sh and enable-ads.sh (needs $DOCKER, getvar, setvar).
# When text runs on Gemini, pick a lighter Gemini model as the backup used while the
# main one is overloaded ("high demand" 503s). It runs on separate capacity at Google.
# Leaves any backup you set yourself alone.
set_ai_fallback() {
  case "$(getvar AI_TEXT_MODEL)" in gemini/*|"") ;; *) return 0 ;; esac
  [ -z "$(getvar AI_TEXT_FALLBACK_MODEL)" ] || return 0
  [ -n "$(getvar AI_PROVIDER_KEY)" ] || return 0
  m=$($DOCKER exec -e GKEY="$(getvar AI_PROVIDER_KEY)" -e MAIN="$(getvar AI_TEXT_MODEL)" postiz-ai python -c "
import json, os, re, urllib.request
url = 'https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000&key=' + os.environ['GKEY']
models = json.load(urllib.request.urlopen(url, timeout=30)).get('models', [])
gen = [m['name'].split('/')[-1] for m in models if 'generateContent' in m.get('supportedGenerationMethods', [])]
def ver(n):
    v = re.search(r'(\d+(?:\.\d+)?)', n); return float(v.group(1)) if v else 0
skip = ('image', 'tts', 'live', 'audio', 'embedding', 'exp', 'preview', 'thinking')
main = os.environ.get('MAIN', '').split('/')[-1]
lite = sorted([n for n in gen if 'flash-lite' in n and not any(s in n for s in skip) and n != main], key=ver, reverse=True)
print(lite[0] if lite else '')
" 2>/dev/null || true)
  if [ -n "$m" ]; then
    setvar AI_TEXT_FALLBACK_MODEL "gemini/$m"
    echo "Backup AI model for busy periods: gemini/$m"
  fi
}
