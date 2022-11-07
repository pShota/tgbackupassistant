from telethon import TelegramClient, events, sync
import sys
import os

if __name__ == '__main__':

    os.chdir("./config")

    if len(sys.argv) < 4:
        print("Input arg: <session name>, <app id>, <app hash>")
        exit(0)
    # start tg client
    tg_client = TelegramClient(str(sys.argv[1]), int(sys.argv[2]), str(sys.argv[3]))
    tg_client.start()
