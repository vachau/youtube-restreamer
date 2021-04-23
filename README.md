# YouTube Restreamer

Automatically monitor a YouTube channel for livestreams and restream them to another YouTube channel or any other streaming service.

*Please do not restream copyrighted material.*

## Setup

### Requirements

 - Python 3.6+
 - FFmpeg (tested with version 4.3+)

### Installation
Download the repository, setup the virtual environment, and install the dependencies
```bash
$ git clone ssh://
$ cd youtube_restreamer
$ python3 -m venv env
$ source env/bin/activate
$ pip install -r requirements.txt
```
Create a configuration file `config.json` specifying the ID of the channel you want to monitor. This can be located on the channel page in the format `https://www.youtube.com/channel/[channel_id]`

```json
{
	"channel_id":  "UCE_M8A5yxnLfW0KghEeajjw"
}
```

### Restreaming to most sites (RTMP)

Restreaming is supported to any site that supports RTMP. Simply specify a nickname for the service and the RTMP url and key in your `config.json`

```json
{
	"services": {
		"trovo": {
			"rtmp_url":  "rtmp://trovo.live/foo",
			"rtmp_key":  "bar"
		}
	}
}
```
Then run the application
```bash
$ python youtube_restreamer.py trovo
```

### Restreaming to YouTube

The application can also automatically create and stream to live broadcasts on YouTube. To enable this you must get OAuth credentials [here](https://console.cloud.google.com/apis/credentials).

1. Create a new project in Google Cloud if one doesn't already exist
2. Under "Library" in the sidebar
	1. Search for "YouTube Data API v3"
	2. Enable it for your project
3. Under "OAuth consent screen" in the sidebar
	1.  Create a consent screen, the more detailed the better. Google is known to disable projects for looking "suspicious"
	2. Under "Scopes" add the scopes `../auth/youtube.force-ssl` and `../auth/youtube`
	3. Under "Test users" add the Google accounts that own the YouTube channels you plan on streaming from
4. Under "Credentials" in the sidebar
	1. Click "Create credentials" at the top
	2. Select: "OAuth client ID"
	3. Select application type: "Desktop app"
	4. Once finished, click the download symbol next to the credentials you created
	5. Add the file to your `config.json`
```json
{
	"youtube_oauth": {
		"secrets_file":  "client_secret.json",
	}
}
```
When running the application for the first time follow the instructions to login with the YouTube channel you would like to stream to.
```bash
$ python youtube_restreamer.py
Please visit this URL to authorize this application: https://...
Enter the authorization code:
```
To switch accounts run with the option `--reset-oauth`

It's recommended to give the application a dedicated channel to prevent it possibly interfering with your other  uploads and streams. 

## Configuration

Options should be specified in the JSON file `config.json` (or a different file with `--config CONFIG_FILE`)

```json
{
	"channel_id":  "UCE_M8A5yxnLfW0KghEeajjw",
	"youtube_oauth": {
		"secrets_file":  "client_secret.json",
		"token_file":  "token.json" // change file that OAuth token is stored in
	},
	"restream_privacy":  "unlisted", // visibility of YouTube restreams
	"restream_title":  "Mirror: %s", // title for YouTube restreams where %s is the source stream's title 
	"services": {
		"trovo": {
			"rtmp_url":  "rtmp://trovo.live/foo",
			"rtmp_key":  "bar"
		}
	},
	"youtube_search_interval":  60 // how often in seconds to fetch the list of streams from channel_id (don't recommend setting this lower than 1 minute)
}
```

It can also be used as a module. Options are provided as a dictionary instead (the keys are the same as above):
```py
from youtube_restreamer import Restreamer

options = {
	"channel_id": "UCE_M8A5yxnLfW0KghEeajjw"
	"youtube_oauth": {
		"secrets_file":  "client_secret.json"
	}
}
restreamer = Restreamer(options)
restreamer.restream()
```
Additional functionality:
```py
>>> restreamer = Restreamer(options, reset_oauth=True)
>>> restreamer.restream("trovo")
>>> restreamer.end_broadcasts()
>>> restreamer.get_channel_id("Y7dpJ0oseIA")
"UCE_M8A5yxnLfW0KghEeajjw"
>>> restreamer.get_channel_id("https://www.youtube.com/watch?v=Y7dpJ0oseIA")
"UCE_M8A5yxnLfW0KghEeajjw"
```

## Limitations

 - The YouTube API limits your request quota to [10,000 "units" a day](https://developers.google.com/youtube/v3/getting-started#quota). Based on the cost of creating and deleting broadcasts, you should be able to create a maximum of  ~100 YouTube restreams each day.
 - There may be a ~5 minute delay between source streams starting and the API detecting them.
 - It's possible for a channel to create multiple concurrent livestreams. Currently this will result in only the most recently created one being restreamed.

## Troubleshooting

 - Youtube-dl errors
	 - Check that your channel_id is valid
	 - Youtube changes their site quite often and youtube-dl is constantly updated to keep up. Try updating it with `pip install -r requirements.txt`
- HttpErrors
	- Make sure livestreaming is enabled on your YouTube channel
- Misc. YouTube
	- Run with `--end-broadcasts` to attempt to end any live broadcasts 
	- Manually end any running live broadcasts on your channel
- Misc. RTMP
	- Ensure that the endpoint is still available and that streams will start automatically on receiving data

