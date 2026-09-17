import os
import time
import subprocess
import gc
from typing import List
from google import genai

# Veo 3.1 の指定可能指定秒数: 4, 6, 8 秒（ショート動画では4秒または8秒が最適）
VEO_FIXED_DURATION = 4

# APIモデルの指定（Preview系モデル）
VEO_MODELS = [
    "veo-3.1-generate-preview",
    "veo-3.1-fast-generate-preview"
]

def generate_veo_clip(prompt: str, output_path: str) -> str:
    """
    Veo 3.1 APIを呼び出し、堅牢なエラーハンドリングとバックオフで4秒動画を生成
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY が設定されていません。")
        
    client = genai.Client(api_key=api_key)
    print(f"[Veo Engine] クリップ生成開始: {prompt[:40]}...")
    
    operation = None
    last_error = None
    
    # モデルごとの試行と429レート制限のリトライ（指数バックオフ）
    for model_name in VEO_MODELS:
        for attempt in range(3):
            try:
                print(f"[Veo Engine] モデル '{model_name}' (試行 {attempt + 1}/3) でリクエスト送信中...")
                operation = client.models.generate_videos(
                    model=model_name,
                    prompt=prompt,
                    config={
                        "aspect_ratio": "9:16",
                        "duration_seconds": VEO_FIXED_DURATION,
                    }
                )
                print(f"[Veo Engine] リクエスト送信成功 (モデル: {model_name})")
                break
            except Exception as e:
                last_error = e
                err_str = str(e)
                print(f"[Veo Engine] エラー発生 ({model_name}): {e}")
                
                # 429 RESOURCE_EXHAUSTED 発生時はクォータのリセットを待機
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    wait_time = (attempt + 1) * 30  # 30s, 60s, 90s バックオフ
                    print(f"[Veo Engine] 429 レート制限検知。{wait_time}秒待機して再試行します...")
                    time.sleep(wait_time)
                else:
                    break
        
        if operation:
            break
            
    if not operation:
        raise RuntimeError(f"すべてのVeoモデルでの動画生成要求に失敗しました: {last_error}")
    
    # 非同期 Operation のポーリング処理
    max_retries = 40
    retries = 0
    while not operation.done and retries < max_retries:
        time.sleep(10)
        operation = client.operations.get(operation)
        retries += 1
        print(f"[Veo Engine] 生成進捗確認中... ({retries}/{max_retries})")
        
    if not operation.done:
        raise TimeoutError("Veo の動画生成がタイムアウトしました。")
        
    # レスポンスデータ構造の抽出フォールバック
    result = getattr(operation, "response", None) or getattr(operation, "result", None)
    if callable(result):
        result = result()

    if not result:
        raise RuntimeError("Veo Operation からレスポンスを取得できませんでした。")

    generated_videos = getattr(result, "generated_videos", None)
    if not generated_videos and hasattr(result, "response"):
        generated_videos = getattr(result.response, "generated_videos", None)

    if not generated_videos:
        raise RuntimeError(f"Veo から動画データが返されませんでした。(Result: {result})")
        
    generated_video = generated_videos[0]
    
    # バイナリデータ（動画本体）の取り出し
    video_bytes = None
    if hasattr(generated_video, "video_bytes") and generated_video.video_bytes:
        video_bytes = generated_video.video_bytes
    elif hasattr(generated_video, "video") and hasattr(generated_video.video, "bytes"):
        video_bytes = generated_video.video.bytes

    if not video_bytes:
        try:
            video_bytes = client.files.download(file=generated_video.video)
        except Exception as e:
            raise RuntimeError(f"動画バイナリの抽出に失敗しました: {e}")

    with open(output_path, "wb") as f:
        f.write(video_bytes)
        
    print(f"[Veo Engine] クリップ保存完了: {output_path}")
    return output_path


def get_audio_duration_ffmpeg(file_path: str) -> float:
    """ffprobe を使用して音声の正確な長さを取得"""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
    return float(result.stdout.strip())


def build_final_video_with_cuts(voice_path: str, video_prompts: List[str], output_path: str = "output_video.mp4") -> str:
    """
    複数クリップ生成・ffmpegによるメモリ超軽量結合および音声合成処理
    """
    if not video_prompts:
        raise ValueError("video_prompts が空です。")

    audio_duration = get_audio_duration_ffmpeg(voice_path)
    print(f"[Video Engine] 音声総尺: {audio_duration:.2f}秒")
    
    generated_files = []
    list_file_path = "ffmpeg_concat_list.txt"
    temp_concat_path = "temp_concat.mp4"
    
    try:
        for i, prompt in enumerate(video_prompts):
            if i > 0:
                print("[Video Engine] レート制限回避のため、次のクリップ生成まで15秒待機...")
                time.sleep(15)
                
            clip_path = f"veo_clip_{i}.mp4"
            generate_veo_clip(prompt, clip_path)
            generated_files.append(clip_path)

        # ffmpeg 用のリストファイル作成
        with open(list_file_path, "w", encoding="utf-8") as f:
            for path in generated_files:
                f.write(f"file '{path}'\n")

        # ffmpeg による無再符号化結合（ストリーミング処理）
        print("[Video Engine] ffmpeg で動画クリップを結合中...")
        concat_cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", list_file_path, "-c", "copy", temp_concat_path
        ]
        subprocess.run(concat_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # 音声の尺に合わせたループ＆トリミング＆音声合成
        print("[Video Engine] ffmpeg で音声合成および最終レンダリング中...")
        final_cmd = [
            "ffmpeg", "-y", "-stream_loop", "-1",
            "-i", temp_concat_path, "-i", voice_path,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-c:a", "aac", "-b:a", "128k",
            "-t", str(audio_duration),
            "-map", "0:v:0", "-map", "1:a:0",
            output_path
        ]
        subprocess.run(final_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print(f"[Video Engine] 最終動画レンダリング成功: {output_path}")

    finally:
        # 一時ファイルのクリーンアップ
        for p in generated_files + [list_file_path, temp_concat_path]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception as e:
                    print(f"一時ファイル削除エラー ({p}): {e}")
        gc.collect()

    return output_path