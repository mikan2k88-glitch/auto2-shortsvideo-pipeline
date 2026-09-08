import os
import json
from google import genai
from google.genai import types

# 1. クライアントの初期化
client = genai.Client(
    api_key=os.environ.get("GEMINI_API_KEY"),
)

tools = [
    {
        'type': 'google_search',
    },
]

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
必ず以下のJSON形式のみで出力してください。マークダウンの ```json やバッククォートは含めず、純粋なJSON文字列のみを出力してください。
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

def clean_and_parse_json(text: str) -> dict:
    """
    モデルの出力からMarkdownのバッククォートなどを除去して安全にJSONをパースする関数
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    
    return json.loads(cleaned)

def generate_youtube_script(theme: str, duration: int = 30) -> dict:
    max_loops = 3
    user_input = f"テーマ: {theme} / 目標尺: {duration}秒のYouTubeショート台本を作成してください。"
    response_text = ""

    for attempt in range(max_loops):
        print(f"\n[AI Agent] 試行回数 {attempt + 1}: 台本生成と品質評価中...")
        
        try:
            # generate_content を使用して直接テキストを取得
            response = client.models.generate_content(
                model='models/gemini-3.8-flash',
                contents=user_input,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    tools=tools,
                    max_output_tokens=65536,
                    thinking_level='medium',
                    response_mime_type='application/json',
                ),
            )
            
            response_text = response.text
            print(f"-> 取得した生テキスト: {response_text[:100]}...")
            
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
