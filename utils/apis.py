import os
from datetime import datetime, timedelta
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
import googleapiclient.discovery
import googleapiclient.errors
import httplib2.error
import youtube_dl

class LiveBroadcast():
    def __init__(self, broadcast_id, title, m3u8_url=None, protocol="m3u8", mine=False):
        self.id = broadcast_id
        self.title = title
        self.m3u8_url = m3u8_url
        self.url = f"https://www.youtube.com/watch?v={broadcast_id}"
        self.protocol = protocol
        self.mine = mine

class GoogleApis:
    class NetworkException(Exception):
        pass

    class HttpException(Exception):
        pass

    class AuthException(Exception):
        pass

    def __init__(self, api_name, api_version, scopes):
        self.api_name = api_name
        self.api_version = api_version
        self.scopes = scopes
        self.service = None

    def is_authorized(self):
        return self.service is not None

    def get_credentials(self, token_file, client_secrets_file, force_new=False):
        creds = None

        # Get previous credentials from file
        if os.path.exists(token_file):
            if not force_new:
                creds = Credentials.from_authorized_user_file(token_file, self.scopes)
            else:
                creds = None
        # If the credentials don't exist, do oauth
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(client_secrets_file, self.scopes)
                creds = flow.run_console()
            with open(token_file, "w") as token:
                token.write(creds.to_json())

        return creds
    
    def auth_key(self, api_key):
        self.service = googleapiclient.discovery.build(self.api_name, self.api_version, developerKey=api_key)

    def auth_oauth(self, token_file, client_secrets_file, force_new=False):
        credentials = self.get_credentials(token_file, client_secrets_file, force_new)
        self.service = googleapiclient.discovery.build(self.api_name, self.api_version, credentials=credentials)

class YoutubeApis(GoogleApis):

    def __init__(self):
        super().__init__("youtube", "v3", ["https://www.googleapis.com/auth/youtube.force-ssl"])

    # Not recommended to use: costs 100 quota units and takes ~5 minutes to detect newly started broadcasts
    def search_livebroadcasts_ytapi(self, channel_id):
        if not self.is_authorized():
            raise GoogleApis.AuthException("Requires OAuth")
        request = self.service.search().list(part="snippet", eventType="live", type="video", channelId=channel_id)
        livestreams = []
        try:
            res = request.execute()
            items = res.get("items", [])
            for item in items:
                single_stream = LiveBroadcast(
                    item.get("id").get("videoId"), 
                    item.get("snippet").get("title")
                )

                livestreams.append(single_stream)
        except googleapiclient.errors.HttpError as e: 
            raise GoogleApis.HttpException(str(e))
        except httplib2.error.ServerNotFoundError as e:
            raise GoogleApis.NetworkException(str(e))
        
        return livestreams

    def search_livebroadcasts(self, channel_id):
        channel_url = f"https://www.youtube.com/channel/{channel_id}"
        options = {
            "playlistend": 1, # only the first item
            "quiet": True
        }
        livestreams = []
        with youtube_dl.YoutubeDL(options) as yt_dl:
            try:
                res = yt_dl.extract_info(channel_url, download=False)
                res_item = res["entries"][0]["entries"][0]

                if res_item["protocol"] == "m3u8":
                    single_stream = LiveBroadcast(
                        res_item["id"],
                        res_item["title"],
                        res_item["url"],
                    )
                    livestreams.append(single_stream)
            except youtube_dl.utils.DownloadError as e: 
                raise GoogleApis.NetworkException(f"youtube-dl failed to search live broadcasts: {str(e)}")
            except (IndexError, KeyError):
                pass # no livestreams found
        return livestreams

    def parse_livestream_res(self, res):
        ingestion_info = res.get("cdn").get("ingestionInfo")
        res_data = {
            "id": res.get("id", ""), # ex 'AniW-ozy_koWoLjDw3F2Rg1618885401806773'
            "rtmp_url": ingestion_info.get("ingestionAddress", ""),
            "rtmp_key": ingestion_info.get("streamName", "")
        }
        return res_data

    def list_videos(self, video_id):
        if not self.is_authorized():
            raise GoogleApis.AuthException("Requires OAuth")
        request = self.service.videos().list(
            part="contentDetails,id,snippet,status",
            id=video_id
        )
        res = None
        try:
            res = request.execute()
        except googleapiclient.errors.HttpError as e:
            raise GoogleApis.HttpException(str(e))
        except httplib2.error.ServerNotFoundError as e:
            raise GoogleApis.NetworkException(str(e))

        return res.get("items")[0]

    # Creates the RTMP ingestion point that can be reused for every stream
    def insert_livestream(self, title, fps="variable", resolution="variable"):
        # fps can be "30fps", "60fps"
        # resolution "1080p", "720p", "480p", etc
        # both can be set to "variable" for automatic detection
        if not self.is_authorized():
            raise GoogleApis.AuthException("Requires OAuth")
        request = self.service.liveStreams().insert(
            # part="snippet,cdn,id,status",
            part = "id,cdn",
            body={
                "cdn": {
                    "ingestionType": "rtmp",
                    "resolution": resolution,
                    "frameRate": fps
                },
                "snippet": {
                    "title": title
                }
            }
        )

        res = None
        try:
            res = request.execute()
        except googleapiclient.errors.HttpError as e:
            raise GoogleApis.HttpException(str(e))
        except httplib2.error.ServerNotFoundError as e:
            raise GoogleApis.NetworkException(str(e))

        return self.parse_livestream_res(res)

    def create_variable_livestream(self, title):
        livestreams = self.list_livestream()
        variable_stream = None
        for livestream in livestreams:
            if livestream.get("cdn").get("resolution") == "variable":
                variable_stream = livestream
                break

        # Seems like YT will always create a default variable stream if deleted
        variable_stream_data = None
        if variable_stream is None:
            print("Creating new variable livestream.")
            variable_stream_data = self.insert_livestream(title)
        else:
            variable_stream_data = self.parse_livestream_res(variable_stream)

        return variable_stream_data  

    def list_livestream(self):
        if not self.is_authorized():
            raise GoogleApis.AuthException("Requires OAuth")
        request = self.service.liveStreams().list(
            part="id,cdn,snippet,status",
            mine=True
        )

        res = None
        try:
            res = request.execute()
        except googleapiclient.errors.HttpError as e:
            raise GoogleApis.HttpException(str(e))
        except httplib2.error.ServerNotFoundError as e:
            raise GoogleApis.NetworkException(str(e))

        return res.get("items", [])

    # Creates the actual stream video instance that viewers see
    def insert_broadcast(self, title, description=None, archive=True, privacy="public"):
        if not self.is_authorized():
            raise GoogleApis.AuthException("Requires OAuth")
        # Privacy may be: "public", "private", "unlisted"
        broadcast_date = datetime.utcnow()
        #broadcast_date += timedelta(minutes=1)

        request = self.service.liveBroadcasts().insert(
            part="id,snippet,contentDetails,status",
            body={
                "contentDetails": {
                    "enableDvr": archive,
                    "enableAutoStart": True,
                    "enableAutoStop": False
                },
                "snippet": {
                    "scheduledStartTime": broadcast_date.isoformat(),
                    "title": title,
                    "description": description
                },
                "status": {
                    "privacyStatus": privacy
                }
            }
        )
        res = None
        try:
            res = request.execute()
        except googleapiclient.errors.HttpError as e:
            raise GoogleApis.HttpException(str(e))
        except httplib2.error.ServerNotFoundError as e:
            raise GoogleApis.NetworkException(str(e))
        
        res_data = {
            "id": res.get("id", "") # ex '1b9GoutrU7k'
        }

        return res_data

    def list_broadcast(self):
        if not self.is_authorized():
            raise GoogleApis.AuthException("Requires OAuth")
        # acceptable status values: complete, live, testing
        request =  self.service.liveBroadcasts().list(
            part="id,snippet,contentDetails,status",
            mine=True
        )
        res = None
        try:
            res = request.execute()
        except googleapiclient.errors.HttpError as e:
            raise GoogleApis.HttpException(str(e))
        except httplib2.error.ServerNotFoundError as e:
            raise GoogleApis.NetworkException(str(e))
        return res.get("items", [])

    def transition_broadcast(self, broadcast_id, status):
        if not self.is_authorized():
            raise GoogleApis.AuthException("Requires OAuth")
        # acceptable status values: complete, live, testing
        request = self.service.liveBroadcasts().transition(
            broadcastStatus=status,
            id=broadcast_id,
            part="id"
        )
        res = None
        try:
            res = request.execute()
        except googleapiclient.errors.HttpError as e:
            raise GoogleApis.HttpException(str(e))
        except httplib2.error.ServerNotFoundError as e:
            raise GoogleApis.NetworkException(str(e))
        return res
        

    def bind_broadcast(self, broadcast_id, stream_id):
        if not self.is_authorized():
            raise GoogleApis.AuthException("Requires OAuth")
        request = self.service.liveBroadcasts().bind(
            id=broadcast_id,
            part="id,snippet,contentDetails,status",
            streamId=stream_id
        )
        res = None
        try:
            res = request.execute()
        except googleapiclient.errors.HttpError as e:
            raise GoogleApis.HttpException(str(e))
        except httplib2.error.ServerNotFoundError as e:
            raise GoogleApis.NetworkException(str(e))
        return res
    
    def create_rtmp_broadcast(self, title, description, privacy):
        # First, check if a stream exists
        stream_data = self.create_variable_livestream("Variable stream")
        broadcast_data = self.insert_broadcast(title, description, privacy=privacy)
        data = {
            "video_id": broadcast_data["id"],
            "rtmp_url": stream_data["rtmp_url"],
            "rtmp_key": stream_data["rtmp_key"]
        }
        self.bind_broadcast(data["video_id"], stream_data["id"])
        return data


    # TODO support other quality levels?
    # TODO distinguish between net and param exceptions
    def get_stream_m3u8_url(self, video_url):
        options = {
            "noplaylist": True,
        }
        playlist_url = None
        with youtube_dl.YoutubeDL(options) as yt_dl:
            try:
                res = yt_dl.extract_info(video_url, download=False)
                playlist_url = res["url"]
            except youtube_dl.utils.DownloadError as e:
                raise GoogleApis.NetworkException(f"youtube-dl failed to download m3u8: {str(e)}")
        return playlist_url

