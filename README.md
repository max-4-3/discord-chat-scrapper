# CHAT SCRAPPER

Scrap discord chat given a channel id  
The token is provided within a `.env` file, in the root of repo:

```ini
TOKEN=YOUR_TOKEN
```

> [!NOTE]
> The token is without surrounding quotes

## USAGE
```console
uv run main.py <channel_id> {save_mode}
```

for example:
```console
uv run main.py 900 json
```


this will save the chats from `900` (channel_id) saving any media encountered along the way in the subdir at `./output/` the media will have
thier own subdir in `media` dir (i.e.: `./output/media/900/<media_item_will_save_here>`) and state + output will be saved under `./output/`
dir as `state_900.json` and `discord_channel_900.json` (NOTE: json here is the output mode (2nd arg to main.py)).


to know more about the options to change/configure pre-defined behaviour checkout `help`:
```console
uv run main.py --help
```
