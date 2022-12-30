import typing

from telethon import TelegramClient, events, sync
import os
import sqlite3
import wand.image
import json
import time
import queue
import re
import shutil

from pyffmpeg import FFmpeg
import PIL.Image
from time import sleep

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


s_api_id = 0
s_api_hash = 0
s_session_name = ""
s_tgclient = None
s_flood_wait_sec = 6


def flush_print(text):
    print(text, flush=True)

# only track stream file
class WatchDogWorker(FileSystemEventHandler):
    def __init__(self, fileQueue: queue):
        self.queue = fileQueue

    def on_closed(self, event):
        if os.path.isfile(event.src_path):

            if not checkValidFile(event.src_path):
                return

            flush_print("closed file:" + event.src_path)
            flush_print("Put into pending queue")
            self.queue.put(event.src_path)


def checkValidFile(filePath):
    base = os.path.basename(filePath)
    if base[0] == '.':
        flush_print("Skip hidden file:" + filePath)
        return False
    elif base == '@eaDir' or '@eaDir' in filePath:
        flush_print("Skip Synology file:" + filePath)
        return False
    elif 'Syno' in filePath or 'SYNO' in filePath or '@SSRECMETA' in filePath:
        flush_print("Skip Synology file:" + filePath)
        return False

    return True


class ScanWorker:
    def __init__(self, configDict: typing.Dict):
        self.script_dir = os.getcwd()
        self.queue = queue.Queue()  # for insert row in multithread
        self.tg_channel = configDict['tg_channel']
        self.target_path = configDict['target_path']
        self.force_send_file = configDict['force_send_file']
        self.temp_folder = os.path.join(self.script_dir, 'tmp')
        self.db_path = os.path.join(self.script_dir, 'config/' + os.path.basename(self.target_path) + ".db")

        # avoid easy flood wait, set entity in api request
        self.channelEntity = s_tgclient.get_entity(self.tg_channel)
        self.initDB()

        # remove old temp folder
        if os.path.exists(self.temp_folder):
            shutil.rmtree(self.temp_folder)
        os.mkdir(self.temp_folder)

    def initDB(self):
        dbCon = sqlite3.connect(self.db_path)
        cur = dbCon.cursor()
        cur.execute("""
                                CREATE TABLE IF NOT EXISTS 'FileStat' (
                                    'file_name'	TEXT NOT NULL,
                                    'modified_date'	TEXT,
                                    'is_uploaded'	INTEGER,
                                    'full_path'	TEXT NOT NULL
                                ) 
                            """)
        dbCon.commit()
        cur.close()
        dbCon.close()

    # flush_printing upload progress
    def progressCallback(self, current, total):
        flush_print(f'Uploaded {current} out of {total} bytes: {format(current / total, ".2%")}')

    def sendFile(self, fileFullPath):
        filename = os.path.basename(fileFullPath)
        try:
            name, file_extension = os.path.splitext(filename)
            if file_extension is not None and file_extension == '.HEIC':
                newFilePath = os.path.join(self.temp_folder, name + '.jpg')
                img = wand.image.Image(filename=fileFullPath)
                img.format = 'jpg'
                img.save(filename=newFilePath)
                img.close()
                res = self.uploadFileTG(newFilePath, self.force_send_file)
                os.remove(newFilePath)
                return res
            else:
                return self.uploadFileTG(fileFullPath, self.force_send_file)

        except Exception as ex:
            flush_print(f'file handling Error on {fileFullPath}:' + str(ex))

        return False

    def uploadFileTG(self, fileFullPath, sendAsFile=False):
        try:
            flush_print("send file:" + fileFullPath)
            filename = os.path.basename(fileFullPath)
            name, file_extension = os.path.splitext(filename)
            file_extension = file_extension.lower()

            size = os.path.getsize(fileFullPath)
            if size > 2147483648:
                flush_print("File larger than 2GB!!")
                return False

            # 10MB max for image in telegram
            sendForFile = True if size > 10485760 or sendAsFile else False

            isVideo = False
            isImage = False

            if '.mov' == file_extension or '.mp4' == file_extension or \
                    '.mkv' == file_extension or '.avi' == file_extension or \
                    '.m4v' == file_extension or '.flv' == file_extension or \
                    '.wmv' == file_extension:
                isVideo = True

            # gif is well supported
            if '.jpg' == file_extension or '.jpeg' == file_extension or \
                    '.png' == file_extension or '.bmp' == file_extension or '.tiff' == file_extension :
                isImage = True


            if isVideo:
                inf = fileFullPath
                outf = os.path.join(self.temp_folder, name + '.jpg')

                ff = FFmpeg()
                ff.convert(inf, outf)

                # creating thumbnail
                image = PIL.Image.open(outf)
                maxsize = (320, 320)
                image.thumbnail(maxsize)
                thumbnail = outf
                image.save(thumbnail)
                image.close()
                flush_print("send as file:" + str(sendForFile))
                s_tgclient.send_file(entity=self.channelEntity, file=fileFullPath, background=True, thumb=thumbnail,
                                        progress_callback=self.progressCallback, force_document=sendForFile)
                os.remove(thumbnail)

            elif isImage:

                if not sendForFile:
                    im = PIL.Image.open(fileFullPath)
                    w, h = im.size
                    if w > 2560 or h > 2560:
                        sendForFile = True
                    im.close()

                # creating thumbnail
                image = PIL.Image.open(fileFullPath)
                maxsize = (320, 320)
                image.thumbnail(maxsize)
                thumbnail = os.path.join(self.temp_folder, name + "_thumb.jpg")
                image.save(thumbnail)
                image.close()

                flush_print("send as file:" + str(sendForFile))
                s_tgclient.send_file(entity=self.channelEntity, file=fileFullPath, background=True, thumb=thumbnail,
                                         progress_callback=self.progressCallback, force_document=sendForFile)
                os.remove(thumbnail)

            else:
                flush_print("send as file:" + str(sendForFile))
                s_tgclient.send_file(entity=self.channelEntity, file=fileFullPath, background=True,
                                         progress_callback=self.progressCallback, force_document=sendForFile)

            flush_print(f"send file completed sleep for {s_flood_wait_sec} sec")
            time.sleep(s_flood_wait_sec)

            return True

        except Exception as ex:
            errStr = str(ex)
            flush_print("Error:" + errStr)
            if errStr.startswith("A wait of"):
                digit = re.findall(r'\d+', errStr)
                if len(digit) > 0:
                    flush_print("Flood wait error happened...wait for " + str(digit[0]) + " seconds..")
                    time.sleep(int(digit[0]))
            elif "DIMENSIONS" in errStr.upper():
                flush_print("Image exceed dimensions, Resend as file")
                self.uploadFileTG(fileFullPath, True)

        return False

    def insertDB(self, dbCur, dbCon, filename, lastModified, fileFullPath):
        try:
            # insert a record
            dbCur.execute(f"""
                            INSERT INTO FileStat VALUES
                                ('{filename}','{lastModified}', false,'{fileFullPath}')
                        """)
            dbCon.commit()
            return True
        except sqlite3.Error as ex:
            flush_print(f'Sql Error on {fileFullPath}:' + str(ex))
        return False

    def updateDBonSuccess(self, dbCur, dbCon, filename, lastModified):
        try:
            # update a record
            dbCur.execute(f"""
                        UPDATE FileStat
                        SET is_uploaded = 1
                        WHERE file_name = '{filename}' AND modified_date = '{lastModified}'
                        """)
            dbCon.commit()
            return True
        except sqlite3.Error as ex:
            flush_print(f'Sql Error on {filename}:' + str(ex))
        return False

    def scanFolder(self):
        flush_print("scan folder:" + self.target_path)

        dbCon = sqlite3.connect(self.db_path)
        cur = dbCon.cursor()
        # iterate over files in
        # that directory
        for root, dirs, files in os.walk(self.target_path):
            for filename in files:

                fileFullPath = os.path.join(root, filename)

                if not checkValidFile(fileFullPath):
                    continue

                lastModified = str(os.path.getmtime(fileFullPath))

                try:
                    # check exist
                    res = cur.execute(
                        f"SELECT * FROM FileStat WHERE file_name='{filename}' AND modified_date='{lastModified}'")
                    record = res.fetchone()

                    if record is None:
                        # insert a record
                        if not self.insertDB(cur, dbCon, filename, lastModified, fileFullPath):
                            continue
                    else:
                        if record[2]:  # uploaded boolean
                            flush_print("Skip uploaded file:" + fileFullPath)
                            continue

                except sqlite3.Error as ex:
                    flush_print(f'Sql Error on {fileFullPath}:' + str(ex))
                    continue

                if self.sendFile(fileFullPath):
                    if not self.updateDBonSuccess(cur, dbCon, filename, lastModified):
                        return False

        flush_print("Done scan all files in:" + self.target_path)
        cur.close()
        dbCon.close()
        return True

    def workOnQueue(self):

        if not self.queue.empty():

            fileFullPath = self.queue.get()

            if not os.path.exists(fileFullPath):
                flush_print(f"Error: {fileFullPath} not exist anymore!")
                return

            dbCon = sqlite3.connect(self.db_path)
            cur = dbCon.cursor()
            filename = os.path.basename(fileFullPath)
            lastModified = str(os.path.getmtime(fileFullPath))

            try:
                # check exist
                res = cur.execute(
                    f"SELECT * FROM FileStat WHERE file_name='{filename}' AND modified_date='{lastModified}'")
                record = res.fetchone()

                if record is None:
                    # insert a record
                    if self.insertDB(cur, dbCon, filename, lastModified, fileFullPath):
                        if self.sendFile(fileFullPath):
                            self.updateDBonSuccess(cur, dbCon, filename, lastModified)
                else:
                    if record[2]:  # uploaded boolean
                        flush_print("Skip uploaded file:" + fileFullPath)
                    else:
                        if self.sendFile(fileFullPath):
                            self.updateDBonSuccess(cur, dbCon, filename, lastModified)

            except sqlite3.Error as ex:
                flush_print(f'Sql Error on {fileFullPath}:' + str(ex))

            cur.close()
            dbCon.close()




if __name__ == '__main__':
    flush_print('backupAssistant.py start!')

    configJsonPath = os.path.join(os.getcwd(), 'config/config.json')
    configInfo = None
    try:
        with open(configJsonPath, 'r') as f:
            configInfo = json.load(f)
    except Exception as exp:
        flush_print(f'IO Error on config.json:' + str(exp))

    if configInfo is None:
        flush_print('Cannot find config.json!')
        exit(1)

    s_api_id = configInfo['api_id']
    s_api_hash = configInfo['api_hash']
    s_session_name = configInfo['session_name']
    s_flood_wait_sec = configInfo['flood_wait_sec']

    # start tg client
    session_file = os.path.join(os.getcwd(), "config/" + s_session_name + '.session')
    flush_print('Using telegram session file:' + session_file)

    if not os.path.exists(session_file):
        flush_print("Please run getSession.py to get the session file and restart the container")
        while True:
            sleep(1000000)

    s_tgclient = TelegramClient(session_file, s_api_id, s_api_hash)
    s_tgclient.start()

    scanWorkers = []
    myObserver = Observer()

    for configDict in configInfo['target_paths']:

        worker = ScanWorker(configDict)

        if 'scan_folder' in configDict and configDict['scan_folder']:
            if not worker.scanFolder():
                flush_print("Error when scaning folder...")

        if 'watchdog' in configDict and configDict['watchdog']:
            myObserver.schedule(WatchDogWorker(worker.queue), path=worker.target_path, recursive=True)
            scanWorkers.append(worker)

    if len(scanWorkers) > 0:
        myObserver.start()
        while True:
            for worker in scanWorkers:
                worker.workOnQueue()
            sleep(5)

    s_tgclient.disconnect()

    flush_print("End of script")
    exit(0)
