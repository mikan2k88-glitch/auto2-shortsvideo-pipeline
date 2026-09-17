import os
import time
import gc
from typing import List
from google import genai

from moviepy.audio.io.AudioFileClip import AudioFileClip
from moviepy.video.io.VideoFileClip import VideoFileClip
from moviepy.video.compositing.CompositeVideoClip import concatenate_videoclips

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


def build_final_video_with_cuts(voice_path: str, video_prompts: List[str], output_path: str = "output_video.mp4") -> str:
    """
    1. 音声の長さ(audio_duration)を取得
    2. 複数のVeo動画クリップを生成
    3. 512MB RAM上限を考慮したメモリ節約結合処理
    """
    if not video_prompts:
        raise ValueError("video_prompts が空です。")

    audio_clip = AudioFileClip(voice_path)
    audio_duration = audio_clip.duration
    print(f"[Video Engine] 音声総尺: {audio_duration:.2f}秒")
    
    generated_files = []
    
    try:
        # 1. Veo クリップを生成
        for i, prompt in enumerate(video_prompts):
            clip_path = f"veo_clip_{i}.mp4"
            generate_veo_clip(prompt, clip_path)
            generated_files.append(clip_path)
            
        # ガベージコレクションでAPI呼び出し時の不要メモリを即座に解放
        gc.collect()

        # 2. MoviePy クリップのオープン（メモリ節約のため解像度低めに処理）
        video_clips = [VideoFileClip(f) for f in generated_files]
        concatenated_video = concatenate_videoclips(video_clips, method="compose")
        
        # 3. トリミング・ループ
        if concatenated_video.duration < audio_duration:
            loop_count = int(audio_duration // concatenated_video.duration) + 1
            final_video = concatenated_video.loop(n=loop_count).subclip(0, audio_duration)
        else:
            final_video = concatenated_video.subclip(0, audio_duration)
            
        final_video = final_video.with_audio(audio_clip)
        
        # 4. RAM超低消費書き出し設定
        final_video.write_videofile(
            output_path,
            fps=15,
            bitrate="1500k",
            codec="libx264",
            audio_codec="aac",
            threads=1,
            preset="ultrafast",
            write_logfile=False,
            logger=None
        )
        print(f"[Video Engine] 最終動画レンダリング成功: {output_path}")
        
    finally:
        # メモリの確実な解放
        try:
            audio_clip.close()
        except:
            pass
            
        for path in generated_files:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception as e:
                    print(f"一時ファイル削除エラー ({path}): {e}")
                    
        gc.collect()

    return output_path