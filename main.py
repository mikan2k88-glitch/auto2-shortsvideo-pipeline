import os
import json
import re
from google import genai

# 1. クライアントの初期化
client = genai.Client(
    api_key=os.environ.get("GEMINI_API_KEY"),
)

# 2. ツール設定
tools = [
    {
        'type': 'google_search',
    },
]

generation_config = {
    'max_output_tokens': 65536,
    'thinking_level': 'medium',
    'response_mime_type': 'application/json',
}

# 3. システムインストラクション
system_instruction = """
# 役割
あなたはYouTubeショートの収益化特化型・台本作成エージェントです。視聴維持率が高く、最後まで見たくなる構成の台本を自動生成します。

# 基本ルール
- 尺（秒数）に応じて、最適な文字数とカット構成を動的に決定してください。
  - 15秒版：約60文字（導入・核心のみ、テンポ最優先）
  - 30秒版：約120文字（起承転結のコンパクト構成）
  - 40秒版：約160〜180文字（詳細な解説・どんでん返しを含むフル構成）
- 収益化・エンゲージメントを高めるため、冒頭3秒で強いフック（疑問・衝撃の事実）を入れ、最後にアクションを促す構成にします。

# 出力フォーマット
必ず以下のJSON形式のみで出力してください。
{
  "target_duration_seconds": 30,
  "hook_score": 85,
  "script_text": "ここに生成された台本の本文が入ります。",
  "cut_allocations": [
    {"start": 0.0, "end": 5.0, "description": "フック映像"},
    {"start": 5.0, "end": 30.0, "description": "本体解説"}
  ]
}
"""

def extract_text_from_step(step) -> str:
    """
    InteractionStep オブジェクト構造からテキスト部分を安全に取り出すヘルパー関数
    """
    if hasattr(step, 'text') and step.text:
        return str(step.text)
    
    # content や output 配下のパーツを再帰的に抽出
    content = getattr(step, 'content', None) or getattr(step, 'output', None)
    if content:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if hasattr(item, 'text') and item.text:
                    parts.append(str(item.text))
                elif isinstance(item, dict) and 'text' in item:
                    parts.append(str(item['text']))
                elif hasattr(item, 'parts'):
                    for p in item.parts:
                        if hasattr(p, 'text') and p.text:
                            parts.append(str(p.text))
            if parts:
                return "".join(parts)
                
    return str(step)

def clean_and_parse_json(raw_text: str) -> dict:
    """
    TextContent等のラッパー表現、Markdown記法、改行制御文字が含まれていても
    最初の '{' から 最後の '}' を抽出して厳格かつ安全にJSON化する関数
    """
    if not raw_text:
        raise ValueError("モデルからの出力テキストが空です。")

    # 最初に見つかる '{' から 最後に見つかる '}' の範囲だけを正確に切り取る
    match = re.search(r'\{.*\}', raw_text, re.DOTALL)
    if not match:
        raise ValueError(f"有効なJSON構造が見つかりませんでした: {raw_text[:200]}")
    
    json_str = match.group(0)

    # strict=False を指定して文字列内の特殊文字・改行文字を許容してパース
    try:
        return json.loads(json_str, strict=False)
    except json.JSONDecodeError:
        # エスケープ文字の調整（タブ変換など）を入れて再試行
        fixed_str = json_str.replace('\t', '\\t')
        return json.loads(fixed_str, strict=False)

def generate_youtube_script(theme: str, duration: int = 30) -> dict:
    max_loops = 3
    user_input = f"テーマ: {theme} / 目標尺: {duration}秒のYouTubeショート台本を作成してください。"
    response_text = ""

    for attempt in range(max_loops):
        print(f"\n[AI Agent] 試行回数 {attempt + 1}: 台本生成と品質評価中...")
        
        try:
            interaction = client.interactions.create(
                model='models/gemini-3.8-flash',
                input=user_input,
                system_instruction=system_instruction,
                tools=tools,
                generation_config=generation_config,
            )
            
            step = interaction.steps[-1]
            response_text = extract_text_from_step(step)

            print(f"-> 取得した生テキスト: {response_text[:80]}...")
            
            result = clean_and_parse_json(response_text)
            
            score = result.get("hook_score", 0)
            print(f"-> 評価スコア: {score}点")
            
            if score >= 80:
                print(f"[Success] スコア基準（80点）をクリアしました！")
                return result
            else:
                print(f"[Retry] スコアが基準未満です。再推敲します...")
                
        except Exception as e:
            print(f"[Error] 処理中にエラーが発生しました: {e}")

    return clean_and_parse_json(response_text)


if __name__ == "__main__":
    target_theme = "現代人が知るべきAI副業の真実"
    target_duration = 30
    
    script_data = generate_youtube_script(theme=target_theme, duration=target_duration)
    
    print("\n=== 最終確定台本データ ===")
    print(json.dumps(script_data, indent=2, ensure_ascii=False))
