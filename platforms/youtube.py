"""يوتيوب: عبر YouTube Data API v3 (رسمي).

يتطلب مشروع على Google Cloud مع تفعيل YouTube Data API وملف OAuth
(client_secrets.json). أول تشغيل يفتح متصفحاً لتسجيل الدخول ويحفظ توكن.
التعليق = كتابة تعليق على فيديو. النشر = رفع فيديو (Shorts أو عادي).
"""
import os
from platforms.base import BasePlatform

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]


class YouTubePlatform(BasePlatform):
    name = "youtube"
    required_fields = []  # يعتمد على client_secrets.json في مجلد البيانات

    def __init__(self, account_row):
        super().__init__(account_row)
        self._youtube = None

    def _api(self):
        if self._youtube is None:
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build
            import config
            secrets = self.creds.get("client_secrets_path") or os.path.join(config.DATA_DIR, "client_secrets.json")
            token_path = os.path.join(config.DATA_DIR, "youtube_token.json")
            credentials = None
            if os.path.exists(token_path):
                from google.oauth2.credentials import Credentials
                credentials = Credentials.from_authorized_user_file(token_path, SCOPES)
            if not credentials or not credentials.valid:
                if credentials and credentials.expired and credentials.refresh_token:
                    credentials.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(secrets, SCOPES)
                    credentials = flow.run_local_server(port=0)
                with open(token_path, "w") as f:
                    f.write(credentials.to_json())
            self._youtube = build("youtube", "v3", credentials=credentials)
        return self._youtube

    def publish(self, text, media_path=None):
        """النشر على يوتيوب = رفع فيديو. text يحتوي عنوان ثم سطر جديد ثم وصف."""
        from googleapiclient.http import MediaFileUpload
        if not media_path:
            raise ValueError("يوتيوب: النشر يتطلب ملف فيديو")
        lines = text.split("\n", 1)
        title, desc = (lines + [""])[0], (lines[1] if len(lines) > 1 else "")
        body = {"snippet": {"title": title[:100], "description": desc, "categoryId": "22"},
                "status": {"privacyStatus": "public"}}
        media = MediaFileUpload(media_path, chunksize=10 * 1024 * 1024, resumable=True)
        self._api().videos().insert(part="snippet,status", body=body, media_body=media).execute()

    def comment(self, video_id, text):
        if not self.comment_limiter.allow():
            raise RuntimeError("تم تجاوز حد التعليقات لهذه الساعة")
        self._api().commentThreads().insert(
            part="snippet",
            body={"snippet": {"videoId": video_id,
                              "topLevelComment": {"snippet": {"textOriginal": text}}}},
        ).execute()

    def like(self, video_id):
        self._api().videos().rate(id=video_id, rating="like").execute()

    def recent_posts(self, channel_name, limit=5):
        try:
            yt = self._api()
            search = yt.search().list(q=channel_name, type="channel", maxResults=1).execute()
            items = search.get("items", [])
            if not items:
                return []
            channel_id = items[0]["id"]["channelId"]
            vids = yt.search().list(channelId=channel_id, type="video", order="date",
                                    maxResults=limit, part="snippet").execute()
            return [{"id": v["id"]["videoId"], "text": v["snippet"]["title"], "image_b64": None}
                    for v in vids.get("items", [])]
        except Exception:
            return []

    def stories(self, username=None):
        return []  # يوتيوب لا يوفر حالات عبر API

    def validate(self):
        try:
            return self._api() is not None
        except Exception:
            return False
