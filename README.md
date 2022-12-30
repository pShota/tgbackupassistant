## Backup files to Telegram

This tool upload files from specific folder in Synology NAS to a specific Telegram channel of your choice.

The script make use of [Telethon](https://github.com/LonamiWebs/Telethon) and [pyffmpeg](https://mhaller.github.io/pyffmpeg/)

It should also works for other linux system with some modification.

## Setup

#### 1. First you need to get API ID and Hash from Telegram

Follow [instructions](https://core.telegram.org/api/obtaining_api_id) to create a app


#### 2. download docker image:

```
docker pull pshota/tgbackupassistant:x86_64-0.3
```

#### 3. setup config.json as following:

```json
{
  "api_id" : <app id>,
  "api_hash" : "<app hash>",
  "session_name" :  "<session name>",
  "flood_wait_sec" : 6,
  "target_paths" : [
    {
      "tg_channel" : "me or invite link",
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

`target_path` is the name of the folder the script will upload files in `target_path` to `tg_channel`

full path of `target_path` should set in `volume` path of docker command

`flood_wait_sec` is the second we wait until next upload file perform, too short telegram will stop you from uploading, 6 seconds is usually the best.

`scan_folder` true to scan all files and upload of target_folder(include subfolder)

`watchdog` : true to enable file monitoring, upload on closed file, suitable for file stream (Surveillance Station)

`force_send_file` will force all file to send as file without any compression.

This config.json will map to container file /app/config/config.json

---

#### 4. create container from image, here is docker-compose file

```
version: "3"
services:
  app:
    container_name: tgbackupassistant
    image: 'pshota/tgbackupassistant:x86_64-0.3'
    volumes:
      - /somepath/tgbackupassistant/config:/app/config
      - <target_path>:/app/<target_path_folder_name>
    mem_limit: 200M
```

the first run will fail because there isn't a telegram session file.

---

#### 5. run getSession.py directly from container to login telegram and get session files

login into your container

  ```
  sudo docker exec -it tgbackupassistant bash
  ```

and run

```
python getSession.py <session name>, <app id>, <app hash>
```

where <session name> is the name you named it in eariler step

after login, it generated a session file `<session name>.session` in /app/config

---

#### 6. restart container again


## Misc

1. Script will change HEIC file to JPG where TG not supporting HEIC

2. file size limit is 2GB and this script will not split larger file (TODO)

3. large resoluton image (>2560 pixel) will automatically send as file, Telegram size limit.

4. only one Telegram account can use per container

5. this script needs to run as root, as it apply `sysctl fs.inotify.max_user_watches=1048576` to increase limit in the beginning
