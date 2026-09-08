import os
import json
import re
import asyncio
import subprocess
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import edge_tts
from google import genai
from youtube_uploader import upload_to_youtube

app = FastAPI(
    title="YouTube Shorts Auto Pipeline API",
    description="台本生成、音声合成、FFmpeg動画合成、YouTube投稿を一括処理するAPI"
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
  "title": "YouTubeショート用タイトル",
  "description": "動画の概要欄説明テキスト #Shorts #AI",
  "script_text": "ここに生成された台本の本文が入ります。",
  "cut_allocations": [
    {"start": 0.0, "end": 5.0, "description": "フック映像"},
    {"start": 5.0, "end": 30.0, "description": "本体解説"}
  ]
}
"""

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
            if hasattr(step, 'text') and step.text:
                response_text = str(step.text)
            elif hasattr(step, 'output') and step.output:
                response_text = str(step.output)
            else:
                response_text = str(step)

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
    """Edge-TTSを使用して日本語ナレーションを生成"""
    voice = "ja-JP-NanamiNeural"
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)
    return output_path

def create_short_video_mp4(audio_path: str, output_mp4_path: str = "output_short.mp4") -> str:
    """
    FFmpegを使って音声ファイル（.mp3）からYouTube Shorts規格（9:16 / 1080x1920）の.mp4動画を生成
    """
    print(f"--- FFmpegによる動画合成を開始: {audio_path} -> {output_mp4_path} ---")
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", "color=c=black:s=1080x1920:r=30",  # 黒背景の縦型動画
        "-i", audio_path,                        # ナレーション音声
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",                             # 音声の長さにぴったり合わせる
        output_mp4_path
    ]
    
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpegエラー: {result.stderr}")
    
    print(f"✨ 動画生成完了: {output_mp4_path}")
    return output_mp4_path

class GenerationRequest(BaseModel):
    theme: str = "YouTubeで収益化できるAI活用法"
    duration: int = 30
    auto_upload: bool = True  # YouTubeへ自動アップロードするかどうか

@app.get("/")
def read_root():
    return {"status": "online", "message": "YouTube Shorts Pipeline API is running."}

@app.post("/generate")
async def generate_short_content(req: GenerationRequest):
    """
    1. Geminiで台本生成
    2. Edge-TTSで音声合成 (.mp3)
    3. FFmpegでShorts縦型動画合成 (.mp4)
    4. YouTube Data APIで自動アップロード
    を一括実行するAPIエンドポイント
    """
    try:
        # 1. Geminiで台本を自動生成
        script_data = generate_youtube_script(theme=req.theme, duration=req.duration)
        
        # 2. 音声ファイル（MP3）の作成
        audio_filename = f"voice_{req.duration}s.mp3"
        await generate_voice_tts(script_data["script_text"], audio_filename)
        
        # 3. 音声から Shorts規格（9:16縦型）の MP4 動画を作成
        video_filename = f"short_{req.duration}s.mp4"
        create_short_video_mp4(audio_filename, video_filename)
        
        script_data["audio_file"] = audio_filename
        script_data["video_file"] = video_filename
        
        # 4. YouTubeへ自動アップロード
        if req.auto_upload:
            try:
                upload_res = upload_to_youtube(
                    video_path=video_filename,
                    script_data=script_data,
                    privacy_status="unlisted"  # 最初は「限定公開」で安全にテスト
                )
                script_data["youtube_upload"] = upload_res
            except Exception as yt_err:
                script_data["youtube_upload_error"] = str(yt_err)
                print(f"⚠️ YouTubeアップロードでエラーが発生しました: {yt_err}")

        return {
            "status": "success",
            "data": script_data
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)