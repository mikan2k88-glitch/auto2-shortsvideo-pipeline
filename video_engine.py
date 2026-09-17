import os
import time
from typing import List
from google import genai
from moviepy.audio.io.AudioFileClip import AudioFileClip
from moviepy.video.io.VideoFileClip import VideoFileClip
from moviepy.video.compositing.concatenate import concatenate_videoclips

# Veo APIの仕様に基づく固定生成秒数
VEO_FIXED_DURATION = 5

def generate_veo_clip(prompt: str, output_path: str) -> str:
    """
    Veo 3.1 Flashlight APIを呼び出し、指定されたプロンプトで5秒の背景動画を生成
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY が設定されていません。")
        
    client = genai.Client(api_key=api_key)
    
    print(f"[Veo Engine] クリップ生成開始: {prompt[:40]}...")
    
    # Veo 3.1 Flashlight 呼び出し
    operation = client.models.generate_videos(
        model="veo-3.1-flashlight-predict-001",
        prompt=prompt,
        config={
            "aspect_ratio": "9:16",
            "duration_seconds": VEO_FIXED_DURATION,
        }
    )
    
    # 完了までポーリング待機（タイムアウト: 最大3分）
    max_retries = 36
    retries = 0
    while not operation.done and retries < max_retries:
        time.sleep(5)
        operation = client.models.get_videos_operation(operation.name)
        retries += 1
        
    if not operation.done:
        raise TimeoutError("Veo 3.1 の動画生成がタイムアウトしました。")
        
    result = operation.result
    if not result.generated_videos:
        raise RuntimeError("Veo 3.1 から動画データが返されませんでした。")
        
    generated_video = result.generated_videos[0]
    
    # バイナリ書き出し
    with open(output_path, "wb") as f:
        f.write(generated_video.video.image_bytes)
        
    print(f"[Veo Engine] クリップ保存完了: {output_path}")
    return output_path


def build_final_video_with_cuts(voice_path: str, video_prompts: List[str], output_path: str = "output_video.mp4") -> str:
    """
    1. 音声の長さ(audio_duration)を取得
    2. 複数のVeo動画クリップを生成
    3. クリップを結合し、音声の末尾ピッタリにトリミングして書き出し
    """
    if not video_prompts:
        raise ValueError("video_prompts が空です。")

    # 1. 音声の正確な長さを取得
    audio_clip = AudioFileClip(voice_path)
    audio_duration = audio_clip.duration
    print(f"[Video Engine] 音声総尺: {audio_duration:.2f}秒")
    
    generated_files = []
    video_clips = []
    
    try:
        # 2. 各プロンプトに基づいてVeo動画クリップを生成
        for i, prompt in enumerate(video_prompts):
            clip_path = f"veo_clip_{i}.mp4"
            generate_veo_clip(prompt, clip_path)
            generated_files.append(clip_path)
            
            # MoviePyオブジェクトとして読み込み
            clip = VideoFileClip(clip_path)
            video_clips.append(clip)
            
        # 3. クリップ群を連結
        concatenated_video = concatenate_videoclips(video_clips, method="compose")
        
        # 4. 音声の尺に合わせてトリミング・ループ補填
        if concatenated_video.duration < audio_duration:
            loop_count = int(audio_duration // concatenated_video.duration) + 1
            final_video = concatenated_video.loop(n=loop_count).subclip(0, audio_duration)
        else:
            final_video = concatenated_video.subclip(0, audio_duration)
            
        final_video = final_video.with_audio(audio_clip)
        
        # 5. Render用 512MB RAM 節約レンダリング設定
        final_video.write_videofile(
            output_path,
            fps=15,
            codec="libx264",
            audio_codec="aac",
            threads=1,
            preset="ultrafast",
            logger=None
        )
        print(f"[Video Engine] 最終動画レンダリング成功: {output_path}")
        
    finally:
        # メモリ解放と一時ファイルのクリーンアップ
        audio_clip.close()
        for clip in video_clips:
            clip.close()
            
        # 一時動画ファイルの破棄
        for path in generated_files:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception as e:
                    print(f"一時ファイル削除エラー ({path}): {e}")

    return output_path
