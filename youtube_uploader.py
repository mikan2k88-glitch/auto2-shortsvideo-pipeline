import os
import datetime
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials

def get_authenticated_service():
    """
    環境変数に設定されたYouTube APIの認証情報を使ってクライアントを生成する
    """
    creds = Credentials(
        token=os.environ.get("YOUTUBE_ACCESS_TOKEN"),
        refresh_token=os.environ.get("YOUTUBE_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ.get("YOUTUBE_CLIENT_ID"),
        client_secret=os.environ.get("YOUTUBE_CLIENT_SECRET"),
        scopes=["https://www.googleapis.com/auth/youtube.upload"]
    )
    return build("youtube", "v3", credentials=creds)

def upload_to_youtube(video_path: str, script_data: dict, privacy_status: str = "unlisted") -> dict:
    """
    動画をYouTubeにアップロードする。
    過去のバージョンが消えないよう、タイムスタンプやバージョンIDを自動付与して別URLとして蓄積する。
    """
    youtube = get_authenticated_service()

    base_title = script_data.get("title", "AI自動生成ショート動画")
    base_desc = script_data.get("description", "#Shorts #AI")
    
    # 比較・検証用として、アップロード日時のタイムスタンプをタイトル・概要欄に自動付与
    now_str = datetime.datetime.now().strftime("%m/%d %H:%M")
    versioned_title = f"{base_title} [{now_str}]"
    
    versioned_desc = (
        f"{base_desc}\n\n"
        f"--- パイプライン検証ログ ---\n"
        f"アップロード日時: {now_str}\n"
        f"システムバージョン: 統合自動プロダクションOS v2.0 (リップシンク・ピクセル制御版)\n"
        f"※過去のバージョンも比較用としてチャンネル内に残しています。"
    )

    body = {
        "snippet": {
            "title": versioned_title,
            "description": versioned_desc,
            "tags": ["Shorts", "AI", "AutomatedPipeline", "Gemini"],
            "categoryId": "28"  # 科学と技術
        },
        "status": {
            "privacyStatus": privacy_status,  # "unlisted" (限定公開) なら過去動画が一般にさらされず比較用に残せます
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(
        video_path,
        chunksize=-1,
        resumable=True
    )

    print(f"YouTubeへのアップロードを開始します... (タイトル: {versioned_title} / 設定: {privacy_status})")
    
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Uploaded {int(status.progress() * 100)}%")

    video_id = response.get("id")
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    
    print(f"アップロード完了！ 比較用URL: {video_url}")
    return {
        "video_id": video_id,
        "video_url": video_url,
        "title": versioned_title
    }
