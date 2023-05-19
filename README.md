## Backup files to Telegram

This tool upload files from specific folder in Synology NAS to a specific Telegram group/channel of your choice.

The script make use of [Telethon](https://github.com/LonamiWebs/Telethon) and [pyffmpeg](https://mhaller.github.io/pyffmpeg/)

This tool only support Telegram bot to upload files. 

It should also work for other linux system with some modification.

## Setup

#### 1. First you need to get API ID and Hash from Telegram

Follow [instructions](https://core.telegram.org/api/obtaining_api_id) to create a app


#### 2. download docker image:

```
docker pull pshota/tgbackupassistant:x86_64-0.5
```

#### 3. setup config.json as following:

```json
{
  "api_id" : <app id>,
  "api_hash" : "<app hash>",
  "session_name" :  "<session name>",
  "bot_token" : "<bot token>",
  "flood_wait_sec" : 6,
  "target_paths" : [
    {
      "tg_channel" : "'me' or group/channel invite link",
      "chat_id" : "<chat_id>",
      "target_path" : "/app/mount_folder",
      "scan_folder" : true,
      "watchdog" : false,
      "force_send_file" : true
    }
  ]
}
```

where:

`api_id` and `api_hash` are the app you created from step 1.

`session_name` will be the session file name generated later, just name it without space for now

`bot_token` is the bot token you created with Telegram Botfather account.

`target_path` is the name of the folder the script will upload files in `target_path` to `tg_channel`

`chat_id` is the group/channel chat id you get from various [methods](https://stackoverflow.com/questions/33858927/how-to-obtain-the-chat-id-of-a-private-telegram-channel)

full path of `target_path` should set in `volume` path of docker command

`flood_wait_sec` is the second we wait until next upload file perform, too short telegram will stop you from uploading, 6 seconds is usually the best.

`scan_folder` true to scan all files and upload of target_folder(include subfolder)

`watchdog` : true to enable file monitoring, upload when a file streaming closed the file, suitable for Surveillance Station

`force_send_file` will force all file to send as file without any compression.

#### 3. Create config folder

and put config.json in it.
This config.json will map to container file /app/config/config.json

---

#### 4. create container from image, here is docker-compose file

```
version: "3"
services:
  app:
    container_name: tgbackupassistant
    image: 'pshota/tgbackupassistant:x86_64-0.5'
    volumes:
      - /somepath/tgbackupassistant/config:/app/config
      - <target_path>:/app/<target_path_folder_name>
    mem_limit: 200M
    restart: on-failure
```


#### 5. create container and run

## Misc

1. Script will change HEIC file to JPG where TG not supporting HEIC

2. file size limit is 2GB and this script will not split larger file (TODO)

3. large resoluton image (>2560 pixel) will automatically send as file, this is Telegram size limit.

4. you need to add `sysctl fs.inotify.max_user_watches=1048576` as a boot up schedule task in Synology, run as root. so that watchdog can monitor more files

5. The container will restart itself if the session is disconnected