import requests
from requests.exceptions import HTTPError  # for improved error handling
from pathlib import Path
from typing import List, Callable, Optional
from requests_toolbelt.multipart.encoder import MultipartEncoder, MultipartEncoderMonitor


class UploadPostClient:
    """Simple wrapper around the Upload-Post REST API."""

    ENDPOINT = "https://api.upload-post.com/api/upload"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("API_KEY missing – add it to a .env file or environment variables.")
        self.headers = {"Authorization": f"Apikey {api_key}"}

    def upload_video(
        self,
        video_path: Path,
        caption: str,
        user: str,
        platforms: Optional[List[str]] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> dict:
        """Upload a single video file to TikTok via Upload-Post.

        Parameters
        ----------
        video_path : Path
            Path to the local .mp4 / .mov file.
        caption : str
            TikTok caption text.
        user : str
            Your Upload-Post username.
        platforms : List[str], optional
            List of platform strings; the Upload-Post API expects a list-style field name, by default None.
        progress_callback : Callable[[int, int], None], optional
            Callback function to handle progress updates, by default None.

        Returns
        -------
        dict
            JSON response parsed into a Python dict.
        """
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        # Prepare multipart fields
        if platforms is None:
            platforms = ["tiktok"]
        # Build fields list including video
        fields: List = [
            ("title", caption),
            ("user", user),
        ]
        for plat in platforms:
            fields.append(("platform[]", plat))
        # Attach video file
        fields.append((
            "video",
            (video_path.name, video_path.open("rb"), "video/mp4"),
        ))

        # Use MultipartEncoder for all uploads (enables progress monitoring)
        encoder = MultipartEncoder(fields=fields)
        # If a callback is provided, wrap in a monitor
        if progress_callback:
            monitor = MultipartEncoderMonitor(encoder, lambda m: progress_callback(m.bytes_read, m.len))
            body = monitor
        else:
            body = encoder

        headers = self.headers.copy()
        headers["Content-Type"] = encoder.content_type

        response = requests.post(
            self.ENDPOINT,
            headers=headers,
            data=body,
            timeout=120,
        )

        # Raise HTTP errors, but convert 401 into a clearer message
        try:
            response.raise_for_status()
        except HTTPError as http_err:
            if response.status_code == 401:
                raise RuntimeError(
                    "Upload-Post API Unauthorized (401) – check your API_KEY and ensure it's correct"
                ) from http_err
            raise
        return response.json() 