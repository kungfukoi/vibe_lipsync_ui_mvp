import os
import urllib.request
import fal_client

IMAGE_PATH = "face.png"
AUDIO_PATH = "line.wav"
OUT_PATH = "output_fabric.mp4"

def download(url: str, out_path: str):
    urllib.request.urlretrieve(url, out_path)

def main():
    # 1) Confirm key exists
    if not os.environ.get("FAL_KEY"):
        raise SystemExit('FAL_KEY not set. In Terminal: export FAL_KEY="..."')

    # 2) Upload local files to fal CDN (returns public URLs)
    print("1) Uploading image...")
    image_url = fal_client.upload_file(IMAGE_PATH)
    print("   image_url:", image_url)

    print("2) Uploading audio...")
    audio_url = fal_client.upload_file(AUDIO_PATH)
    print("   audio_url:", audio_url)

    # 3) Run Fabric
    print("3) Running veed/fabric-1.0 (720p)...")
    result = fal_client.run(
        "veed/fabric-1.0",
        arguments={
            "image_url": image_url,
            "audio_url": audio_url,
            "resolution": "720p",
        },
    )

    # 4) Grab output video URL
    video_url = result["video"]["url"]
    print("4) Video URL:", video_url)

    # 5) Download MP4
    print("5) Downloading MP4...")
    download(video_url, OUT_PATH)
    print(f"DONE: {OUT_PATH}")

if __name__ == "__main__":
    main()