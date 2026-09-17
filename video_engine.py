import os
import time
import subprocess
import gc
from typing import List
from google import genai

VEO_FIXED_DURATION = 4

VEO_MODELS = [
    "veo-3.1-generate-preview",
    "veo-3.1-fast-generate-preview"
]

def generate_veo_clip(prompt: str, output_path: str) -> str:
    """
    Veo 3.1 APIを呼び出し、指定されたプロンプトで4秒の背景動画を生成
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY が設定されていません。")
        
    client = genai.Client(api_key=api_key)
    
    print(f"[Veo Engine] クリップ生成開始: {prompt[:40]}...")
    
    operation = None
    last_error = None
    
    for model_name in VEO_MODELS:
        try:
            print(f"[Veo Engine] モデル '{model_name}' で試行中...")
            operation = client.models.generate_videos(
                model=model_name,
                prompt=prompt,
                config={
                    "aspect_ratio": "9:16",
                    "duration_seconds": VEO_FIXED_DURATION,
                }
            )
            print(f"[Veo Engine] モデル '{model_name}' でのリクエスト成功！")
            break
        except Exception as e:
            print(f"[Veo Engine] モデル '{model_name}' エラー: {e}")
            last_error = e
            continue
            
    if not operation:
        raise RuntimeError(f"すべてのVeoモデルでの動画生成要求に失敗しました: {last_error}")
    
    max_retries = 36
    retries = 0
    while not operation.done and retries < max_retries:
        time.sleep(5)
        operation = client.operations.get(operation)
        retries += 1
        
    if not operation.done:
        raise TimeoutError("Veo の動画生成がタイムアウトしました。")
        
    result = operation.result
    if not result.generated_videos:
        raise RuntimeError("Veo から動画データが返されませんでした。")
        
    generated_video = result.generated_videos[0]
    
    video_bytes = None
    if hasattr(generated_video, "video_bytes") and generated_video.video_bytes:
        video_bytes = generated_video.video_bytes
    elif hasattr(generated_video, "video") and hasattr(generated_video.video, "bytes"):
        video_bytes = generated_video.video.bytes
    elif hasattr(generated_video, "video") and hasattr(generated_video.video, "video_bytes"):
        video_bytes = generated_video.video.video_bytes

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
    """ffprobe を使用して音声の正確な長さを取得（メモリ消費ゼロ）"""
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
    ffmpeg コマンドの直接呼び出しによる超軽量レンダリング（OOM完全回避版）
    """
    if not video_prompts:
        raise ValueError("video_prompts が空です。")

    # 1. 音声尺の取得
    audio_duration = get_audio_duration_ffmpeg(voice_path)
    print(f"[Video Engine] 音声総尺: {audio_duration:.2f}秒")
    
    generated_files = []
    list_file_path = "ffmpeg_concat_list.txt"
    temp_concat_path = "temp_concat.mp4"
    
    try:
        # 2. 各クリップの生成
        for i, prompt in enumerate(video_prompts):
            clip_path = f"veo_clip_{i}.mp4"
            generate_veo_clip(prompt, clip_path)
            generated_files.append(clip_path)

        # 3. ffmpeg 用のファイルリスト作成
        with open(list_file_path, "w", encoding="utf-8") as f:
            for path in generated_files:
                f.write(f"file '{path}'\n")

        # 4. ffmpeg による超軽量動画結合
        print("[Video Engine] ffmpeg で動画クリップを結合中...")
        concat_cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_file_path,
            "-c", "copy",
            temp_concat_path
        ]
        subprocess.run(concat_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # 5. 音声の尺に合わせたループ＆トリミング＆音声合成処理
        print("[Video Engine] ffmpeg で音声合成および最終レンダリング中...")
        final_cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", temp_concat_path,
            "-i", voice_path,
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "28",
            "-c:a", "aac",
            "-b:a", "128k",
            "-t", str(audio_duration),
            "-map", "0:v:0",
            "-map", "1:a:0",
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