import os
import re
import ast
import gc
import json
import subprocess
import asyncio
import edge_tts
from google import genai
from google.genai import types
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from youtube_uploader import upload_to_youtube

app = FastAPI(title="Shorts Video Generation API")

def get_gemini_client():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY 環境変数が設定されていません。")
    return genai.Client(api_key=api_key)

SYSTEM_INSTRUCTION = """
あなたはYouTubeショート動画の専門プロデューサーです。
提供されたテーマから、YouTubeパートナープログラム（YPP）の収益化ポリシーを完全にクリアするオリジナルで魅力的な台本を作成してください。

必ず以下のJSONフォーマットのみを出力してください（余計な装飾テキストや挨拶は不要です）:
{
    "title": "動画のタイトル（インパクト重視）",
    "description": "動画の概要（ハッシュタグを含む）",
    "narration": "ナレーション文章（自然で聞き取りやすく、指定された尺に収まる文字数）"
}
"""

def clean_and_parse_json(text: str) -> dict:
    """Geminiの出力から変則的・崩れたJSONを強力に抽出・補正して辞書型を返す"""
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```\s*', '', text).strip()
    
    # JSON構造を抽出
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        text = match.group(0)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            # シングルクォートなどの代替パース
            data = ast.literal_eval(text)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        raise ValueError(f"JSONのパースに失敗しました: {text[:100]}...")

def generate_script(theme: str, duration: int = 30) -> dict:
    """[台本部門] Gemini APIを使用して収益化対応の台本・概要欄・タイトルを生成"""
    client = get_gemini_client()
    
    char_count = int(duration * 6.5) # 1秒あたり6.5文字換算
    prompt = f"テーマ: {theme}\n目標時間: {duration}秒 (ナレーション文字数: 約{char_count}文字)"

    for attempt in range(1, 4):
        print(f"[AI Agent] 試行回数 {attempt}: 台本生成と品質評価中...")
        try:
            response = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    temperature=0.7
                )
            )
            script_data = clean_and_parse_json(response.text)
            if "title" in script_data and "narration" in script_data:
                print("✨ 台本生成成功！")
                return script_data
        except Exception as e:
            print(f"[Error] 処理中にエラーが発生しました: {e}")
            if attempt == 3:
                raise HTTPException(status_code=500, detail=f"台本生成に失敗しました: {e}")

def generate_auto_theme() -> str:
    """[テーマ自動選定部門] テーマ未指定時、Geminiが収益化可能かつバズるテーマを1つ考案"""
    client = get_gemini_client()
    prompt = """
YouTubeショートで再生数が伸びやすく、視聴者の好奇心を刺激する「1つの具体的テーマ」を考案してください。

【対象ジャンル例（この中からランダムに1つ切り口を選択）】
- 心理学・人間行動・会話術
- 科学・歴史の面白い雑学
- 生活・ヘルスケア・脳科学の豆知識
- お金・経済・インフレの仕組み
- 未来予測・最新テクノロジー

【条件】
- 視聴者が「え、それ本当？」と思わず手を止めるフックのある具体的タイトル/テーマ案
- YouTube収益化ポリシー（YPP）に適合するクリーンで教育的・雑学的な内容
- 挨拶や余計な装飾テキストは一切不要。テーマのテキスト（1行）のみを出力してください。
"""
    try:
        print("--- [テーマ自動選定部門] バズる収益化テーマを考案中... ---")
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.9)
        )
        theme = response.text.strip().replace('"', '').replace('「', '').replace('」', '')
        print(f"✨ 自動考案されたテーマ: {theme}")
        return theme
    except Exception as e:
        print(f"⚠️ テーマ自動生成エラー: {e}")
        return "人間関係が劇的にラクになる心理学的なスルー技術"

async def generate_voice_async(text: str, output_path: str = "output_voice.mp3"):
    """[音声部門] Edge-TTSで日本語ナレーション音声を合成"""
    voice = "ja-JP-NanamiNeural"
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)
    print(f"✨ 音声ファイル作成完了: {output_path}")

def generate_voice(text: str, output_path: str = "output_voice.mp3"):
    asyncio.run(generate_voice_async(text, output_path))

def create_short_video_mp4(audio_path: str, script_data: dict, output_path: str = "output_video.mp4") -> str:
    """FFmpegを使用して音声ファイルとタイトルから9:16のShorts用縦型動画を超軽量モードで自動合成する"""
    print("--- [動画合成部門] FFmpegによる縦型動画(.mp4)の作成を開始（超低メモリモード） ---")
    
    gc.collect()

    title_text = script_data.get("title", "AI Shorts Video")
    clean_title = re.sub(r'[\'":\\]', '', title_text)

    # 540x960 (9:16) 15fps 超軽量縦型動画生成コマンド（512MB無料枠専用）
    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-threads", "1",
        "-f", "lavfi",
        "-i", "color=c=black:s=540x960:r=15",
        "-i", audio_path,
        "-vf", (
            f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
            f"text='{clean_title}':fontcolor=white:fontsize=28:x=(w-text_w)/2:y=(h-text_h)/2:"
            f"box=1:boxcolor=black@0.6:boxborderw=10"
        ),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-c:a", "aac",
        "-b:a", "96k",
        "-shortest",
        output_path
    ]

    try:
        subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print(f"✨ 縦型動画生成完了: {output_path}")
        gc.collect()
        return output_path
    except subprocess.CalledProcessError as e:
        print(f"❌ FFmpegエラー: {e.stderr.decode('utf-8', errors='ignore')}")
        # フォールバック: 超軽量単色背景（720x1280）
        simple_cmd = [
            "ffmpeg", "-y", "-threads", "1",
            "-f", "lavfi", "-i", "color=c=darkblue:s=540x960:r=15",
            "-i", audio_path,
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-shortest", output_path
        ]
        subprocess.run(simple_cmd, check=True)
        gc.collect()
        return output_path

class GenerateRequest(BaseModel):
    theme: str = None  # テーマ未指定(None)の場合は自動生成
    duration: int = 30
    auto_upload: bool = False

@app.get("/")
def read_root():
    return {"message": "AI Short Video Generation API is running!"}

@app.post("/generate")
def generate_endpoint(request: GenerateRequest):
    try:
        # 1. テーマの決定（未指定なら自動考案）
        selected_theme = request.theme if request.theme else generate_auto_theme()
        
        # 2. 台本生成
        script_data = generate_script(selected_theme, request.duration)
        
        # 3. 音声合成
        audio_file = "output_voice.mp3"
        generate_voice(script_data["narration"], audio_file)
        
        # 4. 縦型動画合成 (.mp4)
        video_file = "output_video.mp4"
        create_short_video_mp4(audio_file, script_data, video_file)

        # 5. YouTube自動投稿（フラグ指定 or 環境変数が存在する場合）
        upload_result = None
        has_yt_creds = os.environ.get("YOUTUBE_CLIENT_ID") and os.environ.get("YOUTUBE_REFRESH_TOKEN")
        
        if request.auto_upload or has_yt_creds:
            try:
                print("--- [YouTube投稿部門] 動画の自動投稿を開始 ---")
                upload_result = upload_to_youtube(video_file, script_data, privacy_status="unlisted")
            except Exception as e:
                print(f"⚠️ YouTubeアップロードでエラーが発生しました: {e}")
                upload_result = {"status": "error", "message": str(e)}

        return {
            "status": "success",
            "theme": selected_theme,
            "script": script_data,
            "audio_path": audio_file,
            "video_path": video_file,
            "youtube_upload": upload_result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000)