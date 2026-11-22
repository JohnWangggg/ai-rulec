#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-.sources}"
mkdir -p "$ROOT"

clone() {
  local url="$1" name="$2"
  if [ ! -d "$ROOT/$name/.git" ]; then
    git clone --depth 1 "$url" "$ROOT/$name"
  fi
}

clone https://github.com/v2fly/domain-list-community.git v2fly_domain-list-community
clone https://github.com/jinxinkai/clash-ai-rules.git jinxinkai_clash-ai-rules
clone https://github.com/G4free/clash-ruleset.git G4free_clash-ruleset
clone https://github.com/ma-wenqian/ai-proxy-rules.git ma-wenqian_ai-proxy-rules
clone https://github.com/SukkaW/Surge.git SukkaW_Surge

mkdir -p "$ROOT/bm7"
for name in OpenAI Anthropic Claude Gemini BardAI Copilot; do
  curl -fsSL \
    "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/rule/Clash/$name/$name.yaml" \
    -o "$ROOT/bm7/$name.yaml"
done

mkdir -p "$ROOT/loyalsoldier"
for name in direct proxy reject; do
  curl -fsSL \
    "https://github.com/Loyalsoldier/clash-rules/releases/latest/download/$name.txt" \
    -o "$ROOT/loyalsoldier/$name.txt"
done

echo "sources ready in $ROOT"
