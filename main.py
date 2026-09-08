import os
import json
import re
import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import edge_tts
from google import genai

app = FastAPI(
    title="YouTube Shorts Auto Pipeline API",
    description="Geminiによる可変尺台本自動作成とEdge-TTSによる音声合成API"
)

client = genai.Client(
    api_key=os.environ.get("GEMINI_API_KEY"),
)

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
    """InteractionStepオブジェクト構造からテキスト部分を抽出"""
    if hasattr(step, 'text') and step.text:
        return str(step.text)
    
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
    """生テキストから最初の '{' から 最後の '}' を抽出してJSON化"""
    if not raw_text:
        raise ValueError("モデルからの出力テキストが空です。")

    match = re.search(r'\{.*\}', raw_text, re.DOTALL)
    if not match:
        raise ValueError(f"有効なJSON構造が見つかりませんでした: {raw_text[:200]}")
    
    json_str = match.group(0)

    try:
        return json.loads(json_str, strict=False)
    except json.JSONDecodeError:
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

async def generate_voice_tts(text: str, output_path: str = "output_voice.mp3") -> str:
    """Edge-TTSを使用して日本語ナレーション（七海音声）を生成"""
    voice = "ja-JP-NanamiNeural"
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)
    return output_path

class GenerationRequest(BaseModel):
    theme: str = "現代人が知るべきAI副業の真実"
    duration: int = 30

@app.get("/")
def read_root():
    return {"status": "online", "message": "YouTube Shorts Pipeline API is running."}

@app.post("/generate")
async def generate_short_content(req: GenerationRequest):
    """
    台本生成 ➔ 音声合成を一括実行するAPIエンドポイント
    """
    try:
        # 1. Geminiで台本を自動生成
        script_data = generate_youtube_script(theme=req.theme, duration=req.duration)
        
        # 2. 生成された台本テキストを音声ファイル（MP3）に変換
        audio_filename = f"voice_{req.duration}s.mp3"
        await generate_voice_tts(script_data["script_text"], audio_filename)
        
        script_data["audio_generated"] = True
        script_data["audio_file_path"] = audio_filename
        
        return {
            "status": "success",
            "data": script_data
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    # Render環境のPORT環境変数（デフォルト10000）を取得して常時待機サーバーを起動
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
