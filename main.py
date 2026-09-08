import os
import json
from google import genai
from google.genai import types

# 1. クライアントの初期化（環境変数 GEMINI_API_KEY を自動読み込み）
client = genai.Client(
    api_key=os.environ.get("GEMINI_API_KEY"),
)

# 2. ツールと生成設定
tools = [
    {
        'type': 'google_search',
    },
]

generation_config = {
    'max_output_tokens': 65536,
    'thinking_level': 'medium',
    'response_mime_type': 'application/json',  # 確実にJSONで出力させる設定
}

# 3. システムインストラクション（複数行文字列エラーを防ぐためトリプルクォーテーションに変更）
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

def generate_youtube_script(theme: str, duration: int = 30) -> dict:
    """
    マルチエージェント的発想で、品質スコア（80点以上）を満たすまで最大3回まで再生成・検証する関数
    """
    max_loops = 3
    user_input = f"テーマ: {theme} / 目標尺: {duration}秒のYouTubeショート台本を作成してください。"

    for attempt in range(max_loops):
        print(f"\n[AI Agent] 試行回数 {attempt + 1}: 台本生成と品質評価中...")
        
        try:
            # AI Studioの interactions API を使用
            interaction = client.interactions.create(
                model='models/gemini-3.8-flash',
                input=user_input,
                system_instruction=system_instruction,
                tools=tools,
                generation_config=generation_config,
            )
            
            # レスポンスのテキストを取得
            response_text = interaction.steps[-1].text
            result = json.loads(response_text)
            
            score = result.get("hook_score", 0)
            print(f"-> 評価スコア: {score}点")
            
            # 80点以上なら合格して抜ける
            if score >= 80:
                print(f"[Success] スコア基準（80点）をクリアしました！")
                return result
            else:
                print(f"[Retry] スコアが基準未満です。再推敲します...")
                
        except Exception as e:
            print(f"[Error] 処理中にエラーが発生しました: {e}")

    # 上限に達した場合は最後の結果をフォールバックとして返す
    return json.loads(response_text)


if __name__ == "__main__":
    # テスト実行（例：30秒版で収益化テーマの台本を作る）
    target_theme = "現代人が知るべきAI副業の真実"
    target_duration = 30  # 15, 30, 40 から選択可能
    
    script_data = generate_youtube_script(theme=target_theme, duration=target_duration)
    
    print("\n=== 最終確定台本データ ===")
    print(json.dumps(script_data, indent=2, ensure_ascii=False))
