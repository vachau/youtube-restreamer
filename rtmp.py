from time import sleep

from apis import YoutubeApis
from utils import SubprocessThread, ellipsize

class RtmpServer():
    def __init__(self, url, key):
        self.url = url
        self.key = key
    
    def get_endpoint(self):
        return f"{self.url}/{self.key}"

class RtmpRestream():
    class PollException(Exception):
        pass

    def __init__(self, rtmp_server, stream_file_name, input_m3u8, stream_id, delay=10, rtmp_retry_max=3, dl_retry_max=3):
        self.rtmp_server = rtmp_server
        self.stream_file_name = stream_file_name
        self.input_m3u8 = input_m3u8
        self.stream_id = stream_id
        self.delay = delay
        self.rtmp_retry_max = rtmp_retry_max
        self.dl_retry_max = dl_retry_max
        self.dl_thread = None
        self.rtmp_thread = None
        self.dl_retry_c = 0
        self.rtmp_retry_c = 0

    def __ffmpeg_download_stream(self):
        self.dl_thread = SubprocessThread(["ffmpeg", "-i", self.input_m3u8, "-c", "copy", "-y", self.stream_file_name], "ffmpeg-dl.log")
        self.dl_thread.start()

    def __ffmpeg_send_rtmp(self):
        self.rtmp_thread = SubprocessThread(["ffmpeg", "-re", "-i", self.stream_file_name, "-c", "copy", "-f", "flv", f"{self.rtmp_server.get_endpoint()}"], "ffmpeg-rtmp.log")
        self.rtmp_thread.start()

    def start(self):
        print("Creating thread with ffmpeg downloader")
        self.__ffmpeg_download_stream()
        print(f"Delaying {self.delay} seconds to prevent overrunning file")
        sleep(self.delay)
        print("Creating thread with ffmpeg rtmp client")
        self.__ffmpeg_send_rtmp()


    def stop(self):
        print("Asking ffmpeg subprocesses to exit")
        self.dl_thread.stop()
        self.dl_thread.join()
        self.rtmp_thread.stop()
        self.rtmp_thread.join()
        self.dl_thread = None
        self.rtmp_thread = None

    # gives the status of the subprocess threads
    # returns true if running, false if exited normally
    def poll(self):
        if not self.dl_thread.is_alive():
            self.dl_thread.join()
            print(f"Restream :{self.stream_id}': source stream download failed")
            if self.dl_retry_c >= self.dl_retry_max:
                raise RtmpRestream.PollException(f"Exceeded '{self.dl_retry_max}' max restart attempts for source stream download")
            else:
                print(f"->Retrying {self.dl_retry_c + 1}/{self.dl_retry_max}")
                self.__ffmpeg_download_stream()
                self.dl_retry_c += 1
                return True
        
        if not self.rtmp_thread.is_alive():
            self.rtmp_thread.join()
            print(f"Restream :{self.stream_id}': restream upload failed")
            if self.rtmp_retry_c >= self.rtmp_retry_max:
                raise RtmpRestream.PollException(f"Exceeded '{self.rtmp_retry_max}' max restart attempts for restream upload")
            else:
                print(f"->Retrying {self.rtmp_retry_c + 1}/{self.rtmp_retry_max}")
                self.__ffmpeg_send_rtmp()
                self.rtmp_retry_c += 1
                return True

        # if both alive
        self.dl_retry_c = 0
        self.rtmp_retry_c = 0
        return True

class YoutubeRestream(RtmpRestream):
    def __init__(self, yt_apis, broadcast_id, *args, **kwargs):
        super(YoutubeRestream, self).__init__(*args, **kwargs)
        self.yt_apis = yt_apis
        self.broadcast_id = broadcast_id

    def stop(self):
        super(YoutubeRestream, self).stop()
        print(f"Ending Youtube broadcast '{self.broadcast_id}'")
        self.yt_apis.transition_broadcast(self.broadcast_id, "complete")