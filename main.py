import os
import cv2
import numpy as np
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from playwright.sync_api import sync_playwright

app = Flask(__name__)

# Allow the GitHub Pages frontend to access the API.
CORS(
    app,
    resources={
        r"/*": {
            "origins": ["https://nextdroider.github.io"],
            "methods": ["GET", "POST", "OPTIONS"],
            "allow_headers": ["Content-Type"]
        }
    }
)

BASE_DIR = Path("/home/container")
UPLOAD_DIR = BASE_DIR / "uploads"
VIDEO_DIR = BASE_DIR / "videos"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

WIDTH = 360
HEIGHT = 640
FPS = 30
DURATION = 14


def render_html(source_file, output_file):
    output_file = Path(output_file)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage"
            ]
        )

        try:
            page = browser.new_page(
                viewport={"width": WIDTH, "height": HEIGHT},
                device_scale_factor=1
            )

            page.goto(
                Path(source_file).resolve().as_uri(),
                wait_until="load"
            )
            page.wait_for_timeout(1000)

            stage = page.locator("#stage")

            if stage.count() == 0:
                raise RuntimeError(
                    "HTML must contain an element with id='stage'"
                )

            if not page.evaluate(
                "typeof window.draw === 'function'"
            ):
                raise RuntimeError(
                    "HTML must define window.draw(t)"
                )

            page.evaluate("""
                if (typeof window.raf !== 'undefined') {
                    cancelAnimationFrame(window.raf);
                }
            """)

            width = int(
                stage.evaluate("(el) => el.clientWidth")
            )
            height = int(
                stage.evaluate("(el) => el.clientHeight")
            )

            if width <= 0 or height <= 0:
                raise RuntimeError(
                    "The #stage element has invalid dimensions"
                )

            writer = cv2.VideoWriter(
                str(output_file),
                cv2.VideoWriter_fourcc(*"mp4v"),
                FPS,
                (WIDTH, HEIGHT)
            )

            if not writer.isOpened():
                raise RuntimeError("Could not create MP4 file")

            try:
                total_frames = FPS * DURATION

                for frame_number in range(total_frames):
                    t = frame_number / FPS

                    page.evaluate(
                        "(t) => window.draw(t)",
                        t
                    )

                    image_bytes = stage.screenshot()

                    image_array = np.frombuffer(
                        image_bytes,
                        dtype=np.uint8
                    )

                    frame = cv2.imdecode(
                        image_array,
                        cv2.IMREAD_COLOR
                    )

                    if frame is None:
                        raise RuntimeError(
                            f"Could not decode frame {frame_number}"
                        )

                    frame = cv2.resize(
                        frame,
                        (WIDTH, HEIGHT)
                    )

                    writer.write(frame)

            finally:
                writer.release()

        finally:
            browser.close()

    return output_file


@app.route("/")
def index():
    return jsonify({
        "name": "HTML Animation Render Server",
        "status": "online",
        "upload": "/api/upload",
        "videos": "/api/videos"
    })


@app.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({
            "error": "Missing file field"
        }), 400

    file = request.files["file"]

    if not file.filename:
        return jsonify({
            "error": "Empty filename"
        }), 400

    if not file.filename.lower().endswith(".html"):
        return jsonify({
            "error": "Only HTML files are allowed"
        }), 400

    video_id = os.urandom(8).hex()

    source_path = UPLOAD_DIR / f"{video_id}.html"
    output_path = VIDEO_DIR / f"{video_id}.mp4"

    file.save(source_path)

    try:
        render_html(source_path, output_path)

    except Exception as error:
        source_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)

        app.logger.exception("Video rendering failed")

        return jsonify({
            "error": str(error)
        }), 500

    source_path.unlink(missing_ok=True)

    return jsonify({
        "success": True,
        "id": video_id,
        "video": f"/videos/{video_id}.mp4"
    })


@app.route("/api/videos", methods=["GET"])
def list_videos():
    videos = []

    for video in sorted(VIDEO_DIR.glob("*.mp4")):
        videos.append({
            "id": video.stem,
            "url": f"/videos/{video.name}"
        })

    return jsonify(videos)


@app.route("/videos/<filename>", methods=["GET"])
def get_video(filename):
    if (
        not filename.endswith(".mp4")
        or Path(filename).name != filename
    ):
        return jsonify({
            "error": "Invalid filename"
        }), 400

    return send_from_directory(
        VIDEO_DIR,
        filename
    )


if __name__ == "__main__":
    port = int(
        os.environ.get("PORT")
        or os.environ.get("SERVER_PORT")
        or 12978
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
