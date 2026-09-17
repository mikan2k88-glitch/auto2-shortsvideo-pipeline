import os
import json
import random
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from google import genai
import edge_tts
from supabase import create_client, Client

# 分離した映像エンジンの読み込み
from video_engine import build_final_video_with_cuts

app = FastAPI()

# 環境変数の読み込み
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# 新SDKクライアントの初期化
client = genai.Client(api_key=GEMINI_API_KEY)

# Supabaseクライアントの初期化
supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print(f"Supabase初期化エラー: {e}")

class VideoRequest(BaseModel):
    theme: str = None
    duration: int = 30
    auto_upload: bool = True

def get_existing_themes():
    if not supabase:
        return []
    try:
        response = supabase.table("videos").select("theme").execute()
        return [item["theme"] for item in response.data if "theme" in item]
    except Exception as e:
        print(f"テーマ履歴取得エラー: {e}")
        return []

def save_video_log(theme: str, script: dict, youtube_url: str = None):
    if not supabase:
        return
    try:
        data = {
            "theme": theme,
            "title": script.get("title", ""),
            "narration": script.get("narration", ""),
            "youtube_url": youtube_url,
            "status": "success"
        }
        supabase.table("videos").insert(data).execute()
        print("Supabaseへのデータ保存に成功しました。")
    except Exception as e:
        print(f"Supabase保存エラー: {e}")

def generate_auto_theme():
    existing_themes = get_existing_themes()
    categories = ["行動心理学・脳科学", "歴史の裏説・雑学", "お金・経済のカラクリ", "日常生活の裏技・科学", "健康・人体ミステリー"]
    chosen_category = random.choice(categories)
    
    prompt = f"""
    YouTubeショートでバズる「{chosen_category}」ジャンルのテーマを1つ厳選して提案してください。
    過去のテーマ一覧: {json.dumps(existing_themes, ensure_ascii=False)}
    - テーマ名のみ（20文字以内）で回答してください。
    """
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    return response.text.strip()

@app.post("/generate")
async def generate_video(req: VideoRequest):
    theme = req.theme
    if not theme:
        theme = generate_auto_theme()
    
    system_instruction = """
    あなたはYouTubeショート動画のプロデューサーです。
    指定されたテーマに基づき、動画台本とVeo 3.1（動画生成AI）用の英語プロンプト群を作成してください。
    
    【ルール】
    - narration: 抑揚のある自然な口語体（話し言葉）で記述。
    - video_prompts: 台本のストーリー展開に合わせ、タレント（解説者）が背景と一体となって解説している様子を表現する英語プロンプトを4〜5個の配列で出力してください。
    - 映像スタイルの統一: 9:16 vertical, photorealistic, charismatic Japanese presenter, modern cinematic studio setting を各プロンプトに必ず含めてください。
    
    【出力フォーマット (JSON)】
    {
      "title": "タイトル",
      "description": "概要欄",
      "narration": "ナレーション文章",
      "video_prompts": [
        "A charismatic Japanese male presenter in modern clothing, looking shocked at camera, vertical 9:16, cinematic light",
        "Close up of the presenter explaining with hand gestures, futuristic background, vertical 9:16",
        "Presenter pointing upwards enthusiastically, cinematic atmosphere, vertical 9:16",
        "Presenter smiling and wrapping up the explanation to the viewer, vertical 9:16"
      ]
    }
    """
    
    prompt = f"テーマ「{theme}」で、{req.duration}秒のYouTubeショート動画用コンテンツを作成してください。"
    
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config={
            "system_instruction": system_instruction,
            "response_mime_type": "application/json"
        }
    )
    
    script = json.loads(response.text)
    
    # 2. 音声合成 (Edge-TTS)
    voice_path = "output_voice.mp3"
    communicate = edge_tts.Communicate(script["narration"], "ja-JP-NanamiNeural")
    await communicate.save(voice_path)
    
    # 3. 映像生成・動的カット割り結合 (video_engine)
    video_path = "output_video.mp4"
    video_prompts = script.get("video_prompts", [])
    
    build_final_video_with_cuts(
        voice_path=voice_path,
        video_prompts=video_prompts,
        output_path=video_path
    )
    
    # 4. YouTube自動投稿
    youtube_result = None
    youtube_url = None
    if req.auto_upload:
        from youtube_uploader import upload_to_youtube
        youtube_result = upload_to_youtube(video_path, script)
        if isinstance(youtube_result, dict):
            youtube_url = youtube_result.get("video_url")
    
    # 5. Supabaseへログ保存
    save_video_log(theme, script, youtube_url)
    
    return {
        "status": "success",
        "theme": theme,
        "script": script,
        "audio_path": voice_path,
        "video_path": video_path,
        "youtube_upload": youtube_result
    }
