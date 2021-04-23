import os, json
from argparse import ArgumentParser
from time import sleep

from apis import YoutubeApis, GoogleApis
from utils import SubprocessThread, ellipsize, youtube_link_to_id
from rtmp import RtmpServer, RtmpRestream, YoutubeRestream

class Restreamer():
    class ValidateOptionsException(Exception):
        pass

    class RestreamerException(Exception):
        pass

    def __validate_options(self, options):
        # Optional
        if "services" not in options:
            options["services"] = {}
        if "restream_poll_interval" not in options:
            options["restream_poll_interval"] = 10
        if "youtube_search_interval" not in options:
            options["youtube_search_interval"] = 60
        if "stream_file_name" not in options:
            options["stream_file_name"] = "stream.ts"
        if "restream_title" not in options:
            options["restream_title"] = "%s"
        if "restream_privacy" not in options:
            options["restream_privacy"] = "public"
        else:
            # Validate since youtube api response is unhelpful if wrong
            if not options["restream_privacy"] in ["public", "private", "unlisted"]:
                raise Restreamer.ValidateOptionsException(f"Invalid value '{options['restream_privacy']}' for 'restream_privacy'")
        
        # Required
        if "channel_id" not in options:
            raise Restreamer.ValidateOptionsException("Missing required field 'channel_id'")
        if "youtube_oauth" not in options:
            if options["services"] == {}:
                raise Restreamer.ValidateOptionsException("When not using 'youtube_oauth' you must specify at least one 'services'")
            options["youtube_oauth"] = None
        else:
            if "token_file" not in options["youtube_oauth"]:
                options["youtube_oauth"]["token_file"] = "token.json"
            
        return options

    def __init__(self, options, dev=False, reset_oauth=False):
        if dev:
            os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

        self.options = options
        self.finished_stream_ids = []
        self.__validate_options(self.options)
        self.yt_apis = YoutubeApis()
        if options["youtube_oauth"] is not None:
            self.yt_apis.auth_oauth(self.options["youtube_oauth"]["token_file"], self.options["youtube_oauth"]["secrets_file"], reset_oauth)


    def __end_restream(self, rtmp_restream):
        rtmp_restream.stop()
        self.finished_stream_ids.append(rtmp_restream.stream_id)
        print(f"Ended restream of '{rtmp_restream.stream_id}'")

    def restream(self, service="youtube"):
        try:
            os.remove(self.options["stream_file_name"])
        except OSError:
            pass

        rtmp_server = None
        if service != "youtube":
            service_dict = self.options["services"][service]
            rtmp_server = RtmpServer(service_dict["rtmp_url"], service_dict["rtmp_key"])
        
        rtmp_restream = None
        search_interval_c = self.options["youtube_search_interval"]
        try:
            # Event loop
            while True:
                # Check that stream is still alive
                if rtmp_restream is not None:
                    rtmp_restream_running = False
                    try:
                        rtmp_restream_running = rtmp_restream.poll()
                    except RtmpRestream.PollException as e:
                        print(e)
                    
                    if not rtmp_restream_running:
                        self.__end_restream(rtmp_restream)
                        rtmp_restream = None

                # Get livestreams list
                if search_interval_c >= self.options["youtube_search_interval"]:
                    search_interval_c = 0
                    print(f"Fetching livestreams for channel '{self.options['channel_id']}'")
                    livestreams = None
                    try:
                        livestreams = self.yt_apis.search_livebroadcasts(self.options["channel_id"])
                    except GoogleApis.NetworkException as e:
                        print(e)
                        print("If you are getting 404 errors the channel_id is probably invalid")

                    if livestreams is None:
                        pass
                    elif len(livestreams) > 0:
                        if rtmp_restream is not None:
                            print(f"Currently restreaming '{rtmp_restream.stream_id}'")

                            # Handle stream id change (new stream created during delay)
                            restream_found = False
                            for livestream in livestreams:
                                if livestream.id == rtmp_restream.stream_id:
                                    restream_found = True
                                    break
                            if not restream_found:
                                print("Source stream id changed")
                                self.__end_restream(rtmp_restream)
                                rtmp_restream = None
                        else:
                            source_stream = livestreams[0]
                            print(f"Found source stream '{source_stream.id}'")

                            # Don't recreate source streams that timed out
                            if source_stream.id in self.finished_stream_ids:
                                print("Source stream '{source_stream.id}' already used in a restream, skipping")

                            else:
                                print("Creating restream")
                                stream_m3u8_ellipsized = ellipsize(source_stream.m3u8_url, 75)
                                print(f"m3u8 '{stream_m3u8_ellipsized}'")

                                if rtmp_server is not None:
                                    print(f"Using service '{service}'")
                                    rtmp_restream = RtmpRestream(rtmp_server, self.options["stream_file_name"], source_stream.m3u8_url, source_stream.id)
                                else:
                                    # TODO create a separate object to keep track of a broadcast
                                    print("Using OAuth YouTube account")
                                    # Youtube max title length is 100
                                    broadcast_title = ellipsize(self.options["restream_title"].replace("%s", source_stream.title), 100)
                                    try:
                                        broadcast = self.yt_apis.create_rtmp_broadcast(broadcast_title, self.options["restream_privacy"])
                                        broadcast_id = broadcast["video_id"]
                                        server = RtmpServer(broadcast["rtmp_url"], broadcast["rtmp_key"])
                                        print(f"Created broadcast at 'https://www.youtube.com/watch?v={broadcast_id}'")
                                        rtmp_restream = YoutubeRestream(self.yt_apis, broadcast_id, server, self.options["stream_file_name"], source_stream.m3u8_url, source_stream.id)
                                    except GoogleApis.NetworkException as e:
                                        print(e)
                                    except GoogleApis.HttpException as e:
                                        print(e)
                                        raise Restreamer.RestreamerException("Unable to create new broadcasts, livestreaming is probably disabled on your account")

                                if rtmp_restream is not None:
                                    rtmp_restream.start()
                                print(f"Successfully began restreaming")

                    else:
                        print("No source streams found")
                        if rtmp_restream is not None:
                            self.__end_restream(rtmp_restream)
                            rtmp_restream = None
                    
                search_interval_c += self.options["restream_poll_interval"]
                sleep(self.options["restream_poll_interval"])

        except KeyboardInterrupt as e:
            if rtmp_restream is not None:
                self.__end_restream(rtmp_restream)
                rtmp_restream = None
            raise e  

    def end_broadcasts(self):
        broadcasts = self.yt_apis.list_broadcast()
        print(f"Attempting to end all active broadcasts")
        # For some reason broadcasts remain for a short while after completing
        # TODO check if they're 'complete' first
        transitions_total = len(broadcasts)
        transitions_failed = 0
        for broadcast in broadcasts:
            broadcast_id = broadcast.get("id")
            print(f"Ending broadcast '{broadcast_id}'")
            try:
                self.yt_apis.transition_broadcast(broadcast_id, "complete")
            except GoogleApis.HttpException:
                transitions_failed += 1
                print("\t Failed")
        print(f"{transitions_total - transitions_failed} / {transitions_total} successfully ended")

        # Return false if all transitions failed
        return transitions_failed < transitions_total
        
    def get_channel_id(self, id_or_link):
        video_id = id_or_link
        if len(video_id) != 11:
            video_id = youtube_link_to_id(video_id)
        print(self.yt_apis.list_videos(video_id).get("snippet").get("channelId"))

def main():
    parser = ArgumentParser(description='Automatically download/restream youtube livestreams')
    parser.add_argument("-c", "--config", default="config.json", help="Specify JSON configuration file")
    parser.add_argument("service", nargs="?", metavar="SERVICE", default="youtube", help="Key of server listed in JSON to restream to (leave out for youtube)")
    parser.add_argument("--reset-oauth", action="store_true", dest="reset_oauth", help="Ignore any saved OAuth tokens")
    parser.add_argument("--end-broadcasts", action="store_true", dest="end_broadcasts", help="End all YouTube live broadcasts")
    parser.add_argument("--get-channel-id", metavar="ID_OR_LINK", dest="get_channel_id", help="Retreive channel id from video link or id")

    args = parser.parse_args()

    options = {}
    with open(args.config) as f:
        options = json.load(f)

    restreamer = Restreamer(options, reset_oauth=args.reset_oauth)

    if args.end_broadcasts:
        restreamer.end_broadcasts()
    elif args.get_channel_id is not None:
        restreamer.get_channel_id(args.get_channel_id)
    else:
        restreamer.restream(args.service)

if __name__ == "__main__":
    main()
    