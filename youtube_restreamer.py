import os, json
from argparse import ArgumentParser
from time import sleep
import logging

from utils.apis import YoutubeApis, GoogleApis
from utils.utils import SubprocessThread, ellipsize, youtube_link_to_id, remove_dir_contents, LoggingLevel
from utils.rtmp import RtmpServer, RtmpRestream, YoutubeRestream

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
        if "restream_start_delay" not in options:
            options["restream_start_delay"] = 10
        if "youtube_search_interval" not in options:
            options["youtube_search_interval"] = 120
        if "stream_file_name" not in options:
            options["stream_file_name"] = "stream.ts"
        if "restream_title_format" not in options:
            options["restream_title_format"] = "{title}"
        if "restream_privacy" not in options:
            options["restream_privacy"] = "public"
        if "restream_description_format" not in options:
            options["restream_description_format"] = ""
        if "ffmpeg_bin" not in options:
            options["ffmpeg_bin"] = "ffmpeg"
        if "ffmpeg_log_dir" not in options:
            options["ffmpeg_log_dir"] = None
        if "ffprobe_bin" not in options:
            options["ffprobe_bin"] = "ffprobe"
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
        if self.options["ffmpeg_log_dir"]:
            try:
                os.mkdir(self.options["ffmpeg_log_dir"])
            except FileExistsError:
                pass
            remove_dir_contents(self.options["ffmpeg_log_dir"])

    def __format_restream_field(self, live_broadcast, placeholder):
        # TODO find a cleaner way to do this
        return placeholder.replace("{title}", live_broadcast.title).replace("{url}", live_broadcast.url).replace("{channel_name}", live_broadcast.channel_name).replace("{channel_url}", live_broadcast.channel_url)

    def __end_restream(self, rtmp_restream):
        rtmp_restream.stop()
        self.finished_stream_ids.append(rtmp_restream.stream_id)
        logging.info(f"Ended restream of '{rtmp_restream.stream_id}'")

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
                        logging.error(e)
                    
                    if not rtmp_restream_running:
                        self.__end_restream(rtmp_restream)
                        rtmp_restream = None

                # Get livestreams list
                if search_interval_c >= self.options["youtube_search_interval"]:
                    search_interval_c = 0
                    logging.info(f"Fetching livestreams for channel '{self.options['channel_id']}'")
                    livestreams = None
                    try:
                        livestreams = self.yt_apis.search_livebroadcasts(self.options["channel_id"])
                    except GoogleApis.NetworkException as e:
                        logging.error(e)
                        logging.warning("If you are getting 404 errors the channel_id is probably invalid")

                    if livestreams is None:
                        pass
                    elif len(livestreams) > 0:
                        if rtmp_restream is not None:
                            logging.info(f"Currently restreaming '{rtmp_restream.stream_id}'")

                            # Handle stream id change (new stream created during delay)
                            restream_found = False
                            for livestream in livestreams:
                                if livestream.id == rtmp_restream.stream_id:
                                    restream_found = True
                                    break
                            if not restream_found:
                                logging.warning("Source stream id changed")
                                self.__end_restream(rtmp_restream)
                                rtmp_restream = None
                        else:
                            source_stream = livestreams[0]
                            logging.info(f"Found source stream '{source_stream.id}'")

                            # Don't recreate source streams that timed out
                            if source_stream.id in self.finished_stream_ids:
                                logging.info(f"Source stream '{source_stream.id}' already used in a restream, skipping")

                            else:
                                logging.info("Creating restream")
                                stream_m3u8_ellipsized = ellipsize(source_stream.m3u8_url, 75)
                                logging.info(f"m3u8 '{stream_m3u8_ellipsized}'")

                                if rtmp_server is not None:
                                    logging.info(f"Using service '{service}'")
                                    rtmp_restream = RtmpRestream(rtmp_server, 
                                        self.options["stream_file_name"], 
                                        source_stream.m3u8_url, source_stream.id, 
                                        log_dir=self.options["ffmpeg_log_dir"],
                                        ffmpeg_bin=self.options["ffmpeg_bin"],
                                        ffprobe_bin=self.options["ffprobe_bin"],
                                        delay=self.options["restream_start_delay"]
                                    )
                                else:
                                    # TODO create a separate object to keep track of a broadcast
                                    logging.info("Using OAuth YouTube account")
                                    # Youtube max title length is 100
                                    broadcast_title = ellipsize(self.__format_restream_field(source_stream, self.options["restream_title_format"]), 100)
                                    broadcast_desc = self.__format_restream_field(source_stream, self.options["restream_description_format"])
                                    try:
                                        broadcast = self.yt_apis.create_rtmp_broadcast(broadcast_title, broadcast_desc, self.options["restream_privacy"])
                                        broadcast_id = broadcast["video_id"]
                                        server = RtmpServer(broadcast["rtmp_url"], broadcast["rtmp_key"])
                                        logging.info(f"Created broadcast at 'https://www.youtube.com/watch?v={broadcast_id}'")
                                        rtmp_restream = YoutubeRestream(self.yt_apis, 
                                            broadcast_id, 
                                            server, 
                                            self.options["stream_file_name"], 
                                            source_stream.m3u8_url, 
                                            source_stream.id, 
                                            log_dir=self.options["ffmpeg_log_dir"],
                                            ffmpeg_bin=self.options["ffmpeg_bin"],
                                            ffprobe_bin=self.options["ffprobe_bin"],
                                            delay=self.options["restream_start_delay"]
                                        )
                                    except GoogleApis.NetworkException as e:
                                        logging.error(e)
                                    except GoogleApis.HttpException as e:
                                        logging.critical(e)
                                        raise Restreamer.RestreamerException("Unable to create new broadcasts, livestreaming is probably disabled on your account")

                                if rtmp_restream is not None:
                                    rtmp_restream.start()
                                logging.info(f"Successfully began restreaming")

                    else:
                        logging.info("No source streams found")
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
        logging.info(f"Attempting to end all active broadcasts")
        # For some reason broadcasts remain for a short while after completing
        # TODO check if they're 'complete' first
        transitions_total = len(broadcasts)
        transitions_failed = 0
        for broadcast in broadcasts:
            broadcast_id = broadcast.get("id")
            logging.info(f"Ending broadcast '{broadcast_id}'")
            try:
                self.yt_apis.transition_broadcast(broadcast_id, "complete")
            except GoogleApis.HttpException:
                transitions_failed += 1
                logging.warning("->Failed")
        logging.info(f"{transitions_total - transitions_failed}/{transitions_total} successfully ended")

        # Return false if all transitions failed
        return transitions_failed < transitions_total

def main():
    parser = ArgumentParser(description='Automatically download/restream youtube livestreams')
    parser.add_argument("-c", "--config", default="config.json", help="Specify JSON configuration file")
    parser.add_argument("service", nargs="?", metavar="SERVICE", default="youtube", help="Key of server listed in JSON to restream to (leave out for youtube)")
    parser.add_argument("--reset-oauth", action="store_true", dest="reset_oauth", help="Ignore any saved OAuth tokens")
    parser.add_argument("--end-broadcasts", action="store_true", dest="end_broadcasts", help="End all YouTube live broadcasts")
    parser.add_argument("--quiet", action="store_true", help="Don't print any output")
    parser.add_argument("--log-level", choices=LoggingLevel.LEVELS_KEYS, default=None, dest="log_level", help="Set logging level")

    args = parser.parse_args()

    if args.quiet:
        logging.basicConfig(level=logging.CRITICAL, format="%(message)s")
    if args.log_level is None:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    else:
        logging_level = LoggingLevel(args.log_level)
        logging.basicConfig(level=logging_level.level)

    options = {}
    with open(args.config) as f:
        options = json.load(f)

    restreamer = Restreamer(options, reset_oauth=args.reset_oauth)

    if args.end_broadcasts:
        restreamer.end_broadcasts()
    else:
        restreamer.restream(args.service)

if __name__ == "__main__":
    main()
    