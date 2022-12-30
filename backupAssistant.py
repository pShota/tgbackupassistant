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
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from pyffmpeg import FFmpeg
import PIL.Image
from time import sleep
import psutil


s_api_id = 0
s_api_hash = 0
s_session_name = ""
s_tgclient = None
s_flood_wait_sec = 6


def has_handle(fpath):
    for proc in psutil.process_iter():
        try:
            for item in proc.open_files():
                if fpath == item.path:
                    return True
        except Exception:
            pass

    return False


def checkValidFile(filePath):
    base = os.path.basename(filePath)
    if base[0] == '.':
        print("Skip hidden file:" + filePath)
        return False
    elif base == '@eaDir' or '@eaDir' in filePath:
        print("Skip Synology file:" + filePath)
        return False
    elif 'Syno' in filePath or 'SYNO' in filePath:
        print("Skip Synology file:" + filePath)
        return False
    return True


class WatchDogWorker(FileSystemEventHandler):
    def __init__(self, fileQueue: queue):
        self.queue = fileQueue

    # def on_any_event(self, event):
    #     print(f"event_type:{event.event_type} {event.src_path}")

    # watch dog callback, on different thread
    def on_modified(self, event):
        if os.path.isfile(event.src_path):

            print("modified file:", event.src_path)
            if not checkValidFile(event.src_path):
                return
            print("Put into pending queue")
            self.queue.put(event.src_path)


class ScanWorker:
    def __init__(self, configDict: typing.Dict, fileQueue: queue):
        self.script_dir = os.getcwd()
        self.queue = fileQueue
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




    # Printing upload progress
    def progressCallback(self, current, total):
        print('Uploaded', current, 'out of', total, 'bytes: {:.2%}'.format(current / total))

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
            print(f'file handling Error on {fileFullPath}:' + str(ex))

        return False

    def uploadFileTG(self, fileFullPath, sendAsFile=False):
        try:
            print("send file:" + fileFullPath)
            filename = os.path.basename(fileFullPath)
            name, file_extension = os.path.splitext(filename)
            file_extension = file_extension.lower()

            size = os.path.getsize(fileFullPath)
            if size > 2147483648:
                print("File larger than 2GB!!")
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
                print("send as file:" + str(sendForFile))
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

                print("send as file:" + str(sendForFile))
                s_tgclient.send_file(entity=self.channelEntity, file=fileFullPath, background=True, thumb=thumbnail,
                                         progress_callback=self.progressCallback, force_document=sendForFile)
                os.remove(thumbnail)

            else:
                print("send as file:" + str(sendForFile))
                s_tgclient.send_file(entity=self.channelEntity, file=fileFullPath, background=True,
                                         progress_callback=self.progressCallback, force_document=sendForFile)

            print(f"send file completed sleep for {s_flood_wait_sec} sec")
            time.sleep(s_flood_wait_sec)

            return True

        except Exception as ex:
            errStr = str(ex)
            print("Error:" + errStr)
            if errStr.startswith("A wait of"):
                digit = re.findall(r'\d+', errStr)
                if len(digit) > 0:
                    print("Flood wait error happened...wait for " + str(digit[0]) + " seconds..")
                    time.sleep(int(digit[0]))
            elif "DIMENSIONS" in errStr.upper():
                print("Image exceed dimensions, Resend as file")
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
            print(f'Sql Error on {fileFullPath}:' + str(ex))
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
            print(f'Sql Error on {filename}:' + str(ex))
        return False

    def scanFolder(self):
        print("scan folder:" + self.target_path)

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
                            print("Skip uploaded file:" + fileFullPath)
                            continue

                except sqlite3.Error as ex:
                    print(f'Sql Error on {fileFullPath}:' + str(ex))
                    continue

                if self.sendFile(fileFullPath):
                    if not self.updateDBonSuccess(cur, dbCon, filename, lastModified):
                        return False

        print("Done scan all files in:" + self.target_path)
        cur.close()
        dbCon.close()
        return True

    def workOnQueue(self):
        if not self.queue.empty():
            # only process 1 item at a time, give other worker chance to upload
            # while all workers share the same tg session (thus they can't upload in parallel)
            fileFullPath = self.queue.get()

            # to avoid race condition
            if has_handle(fileFullPath):
                print("Waiting file to finish writing:" + fileFullPath)
                time.sleep(2)

            if not os.path.exists(fileFullPath):
                print(f"Error: {fileFullPath} not exist anymore!")
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
                        print("Skip uploaded file:" + fileFullPath)
                    else:
                        if self.sendFile(fileFullPath):
                            self.updateDBonSuccess(cur, dbCon, filename, lastModified)

            except sqlite3.Error as ex:
                print(f'Sql Error on {fileFullPath}:' + str(ex))

            cur.close()
            dbCon.close()
        return self.queue.empty()

if __name__ == '__main__':
    print('backupAssistant.py start!')

    configJsonPath = os.path.join(os.getcwd(), 'config/config.json')
    configInfo = None
    try:
        with open(configJsonPath, 'r') as f:
            configInfo = json.load(f)
    except Exception as exp:
        print(f'IO Error on config.json:' + str(exp))

    if configInfo is None:
        print('Cannot find config.json!')
        exit(1)

    s_api_id = configInfo['api_id']
    s_api_hash = configInfo['api_hash']
    s_session_name = configInfo['session_name']
    s_flood_wait_sec = configInfo['flood_wait_sec']

    # start tg client
    session_file = os.path.join(os.getcwd(), "config/" + s_session_name + '.session')
    print('Using telegram session file:' + session_file)

    if not os.path.exists(session_file):
        print("Please run getSession.py to get the session file and restart the container")
        while True:
            sleep(1000000)

    s_tgclient = TelegramClient(session_file, s_api_id, s_api_hash)
    s_tgclient.start()

    myObserver = Observer()
    watchdogWorkers = []
    for configDict in configInfo['target_paths']:
        insertQueue = queue.Queue()  # for insert row in multithread
        worker = ScanWorker(configDict, insertQueue)

        if 'scan_folder' in configDict and configDict['scan_folder']:
            if not worker.scanFolder():
                print("Error when scaning folder...")

        if 'watchdog' in configDict and configDict['watchdog']:
            watchdogWorkers.append(worker)
            # create watchdog and insert new file to queue on event
            # myObserver.schedule(WatchDogWorker(insertQueue), path=worker.target_path, recursive=True)

    if len(watchdogWorkers) > 0:
        print("Start watchdog")
        myObserver.start()
        while True:
            try:
                allQueueClear = True
                for worker in watchdogWorkers:
                    if not worker.workOnQueue():  # queue not empty
                        allQueueClear = False
                if allQueueClear:
                    time.sleep(5)

            except Exception as ex:
                myObserver.stop()
                print(str(ex))

    s_tgclient.disconnect()

    print("End of script")
    exit(0)
