"""منصة X (تويتر): عبر Tweepy و API v2 الرسمي.

يتطلب حساب مطوّر من developer.x.com. انتبه: الوصول المجاني محدود جداً،
وإرسال الردود والجلب بكميات يحتاج عادة باقة مدفوعة. راجع الباقات في حسابك.
"""
import tweepy
from platforms.base import BasePlatform, RateLimitExceeded


class XPlatform(BasePlatform):
    name = "x"
    required_fields = ["api_key", "api_secret", "access_token", "access_token_secret"]

    def __init__(self, account_row):
        super().__init__(account_row)
        auth = tweepy.OAuth1UserHandler(
            self.creds["api_key"],
            self.creds["api_secret"],
            self.creds["access_token"],
            self.creds["access_token_secret"],
        )
        self.media_api = tweepy.API(auth)
        self.client = tweepy.Client(
            consumer_key=self.creds["api_key"],
            consumer_secret=self.creds["api_secret"],
            access_token=self.creds["access_token"],
            access_token_secret=self.creds["access_token_secret"],
            wait_on_rate_limit=True,
        )

    def publish(self, text, media_path=None):
        if media_path:
            media = self.media_api.media_upload(filename=media_path)
            self.client.create_tweet(text=text, media_ids=[media.media_id_string])
        else:
            self.client.create_tweet(text=text)

    def comment(self, post_id, text):
        """التعليق على X هو رد على تغريدة."""
        if not self.comment_limiter.allow():
            raise RateLimitExceeded("تم تجاوز حد الردود لهذه الساعة")
        self.client.create_tweet(text=text, in_reply_to_tweet_id=post_id)

    def like(self, post_id):
        if not self.like_limiter.allow():
            raise RateLimitExceeded("تم تجاوز حد الإعجابات لهذه الساعة")
        self.client.like(post_id)

    def recent_posts(self, username, limit=5):
        try:
            user = self.client.get_user(username=username)
            if not user or not user.data:
                return []
            res = self.client.get_users_tweets(user.data.id, max_results=min(limit, 5),
                                               tweet_fields=["text"])
            return [{"id": t.id, "text": t.text, "image_b64": None}
                    for t in (res.data or [])] if res and res.data else []
        except Exception:
            return []

    def stories(self, username=None):
        return []  # لا توجد حالات على X

    def validate(self):
        try:
            return self.client.get_me().data is not None
        except Exception:
            return False
