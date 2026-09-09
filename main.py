import os
import json
import random
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import google.generativeai as genai
import edge_tts
from supabase import create_client, Client

# MoviePy v2.0+ 対応インポート
from moviepy.audio.io.AudioFileClip import AudioFileClip
from moviepy.video.VideoClip import ColorClip

app = FastAPI()

# 環境変数の読み込み
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

genai.configure(api_key=GEMINI_API_KEY)

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
    """Supabaseから過去に生成したテーマ一覧を取得"""
    if not supabase:
        return []
    try:
        response = supabase.table("videos").select("theme").execute()
        return [item["theme"] for item in response.data if "theme" in item]
    except Exception as e:
        print(f"テーマ履歴取得エラー: {e}")
        return []

def save_video_log(theme: str, script: dict, youtube_url: str = None):
    """生成結果をSupabaseに保存"""
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
    """過去のテーマと被らないバズりテーマを自動生成"""
    existing_themes = get_existing_themes()
    
    categories = ["行動心理学・脳科学", "歴史の裏説・雑学", "お金・経済のカラクリ", "日常生活の裏技・科学", "健康・人体ミステリー"]
    chosen_category = random.choice(categories)
    
    prompt = f"""
    YouTubeショートでバズる「{chosen_category}」ジャンルのテーマを1つ厳選して提案してください。
    
    【条件】
    - 過去に作成した以下のテーマとは絶対に被らない新しいテーマにしてください。
    過去のテーマ一覧: {json.dumps(existing_themes, ensure_ascii=False)}
    - テーマ名のみ（20文字以内）で回答してください。説明文は不要です。
    """
    
    model = genai.GenerativeModel("gemini-3.8-flash")
    response = model.generate_content(prompt)
    return response.text.strip()

@app.post("/generate")
async def generate_video(req: VideoRequest):
    # テーマ未指定の場合は自動生成（重複排除付き）
    theme = req.theme
    if not theme:
        theme = generate_auto_theme()
    
    # 1. 台本生成 (Gemini API)
    system_instruction = """
    あなたはYouTubeショート動画のヒットメーカーです。
    視聴者のスクロールの手を止め、最後まで離脱させない構成で台本を作成してください。
    
    【出力フォーマット】
    JSON形式で出力してください:
    {
      "title": "インパクトのあるタイトル",
      "description": "概要欄（ハッシュタグ含む）",
      "narration": "ナレーション文章"
    }
    """
    
    prompt = f"テーマ「{theme}」で、{req.duration}秒のYouTubeショート動画の台本を作成してください。"
    model = genai.GenerativeModel("gemini-3.8-flash", system_instruction=system_instruction)
    response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
    
    script = json.loads(response.text)
    
    # 2. 音声合成 (Edge-TTS)
    voice_path = "output_voice.mp3"
    communicate = edge_tts.Communicate(script["narration"], "ja-JP-NanamiNeural")
    await communicate.save(voice_path)
    
    # 3. 実際の動画ファイル生成 (MoviePy)
    video_path = "output_video.mp4"
    audio_clip = AudioFileClip(voice_path)
    
    # 縦型ショート動画サイズ (1080x1920)・黒背景
    video_clip = ColorClip(size=(1080, 1920), color=(0, 0, 0), duration=audio_clip.duration)
    video_clip = video_clip.with_audio(audio_clip)
    
    # MP4ファイルとしてレンダリング出力
    video_clip.write_videofile(
        video_path,
        fps=24,
        codec="libx264",
        audio_codec="aac",
        logger=None
    )
    
    # クリップのリソース解放
    audio_clip.close()
    video_clip.close()
    
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
